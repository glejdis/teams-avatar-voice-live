"""costboard — a tiny dashboard for per-call cost records.

Reads the ``callcosts`` Azure Table (written by every transport's
:class:`core.cost.CostSink`) and serves:

- ``GET /``            — HTML table of runs with filters + running totals
- ``GET /api/runs``    — JSON list of runs (filtered)
- ``GET /api/summary`` — JSON aggregates (totals by transport/channel/persona/day)
- ``GET /export.csv``  — CSV of the filtered runs
- ``GET /healthz``     — liveness probe

Auth to the table follows the same precedence as the writer
(``core.cost.TableCostSink``): connection string → account key → AAD
(Managed Identity / ``DefaultAzureCredential``). Configure via the same
``COST_STORE_*`` env vars so one ``.env`` drives both the app and the board.

Run locally:

    cd costboard
    python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt
    .venv\\Scripts\\python app.py        # http://localhost:3100
"""

from __future__ import annotations

import csv
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent

load_dotenv(REPO_ROOT / ".env", override=False)
load_dotenv(ROOT / ".env", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("costboard")

TABLE_NAME = os.getenv("COST_STORE_TABLE", "callcosts").strip() or "callcosts"

# Numeric columns we surface in the UI / aggregates.
NUMERIC_FIELDS = (
    "totalUsd", "realtimeUsd", "avatarUsd", "visionUsd", "acsUsd",
    "elapsedSeconds", "avatarMinutes", "acsMinutes",
    "rtAudioInTokens", "rtAudioOutTokens", "rtTextInTokens", "rtTextOutTokens",
    "visionCalls", "visionInTokens", "visionOutTokens",
)


# ─────────────────────────────────────────────────────────────────────────────
# Table access
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_endpoint() -> Optional[str]:
    endpoint = os.getenv("COST_STORE_ENDPOINT", "").strip()
    if endpoint:
        return endpoint
    account = os.getenv("COST_STORE_ACCOUNT", "").strip()
    if account:
        return f"https://{account}.table.core.windows.net"
    return None


def _get_table_client():
    """Build a sync ``TableClient`` from COST_STORE_* env, or return None if
    the store isn't configured (the UI then shows an empty, explained state).

    The Azure SDK is imported lazily *after* confirming the store is configured,
    so an unconfigured board runs without ``azure-data-tables`` installed."""
    conn = os.getenv("COST_STORE_CONNECTION_STRING", "").strip()
    endpoint = _resolve_endpoint()
    if not (conn or endpoint):
        return None

    from azure.data.tables import TableClient

    if conn:
        return TableClient.from_connection_string(conn, table_name=TABLE_NAME)

    key = os.getenv("COST_STORE_KEY", "").strip()
    if key:
        from azure.core.credentials import AzureNamedKeyCredential

        account_name = endpoint.split("//", 1)[-1].split(".", 1)[0]
        cred = AzureNamedKeyCredential(account_name, key)
        return TableClient(endpoint=endpoint, table_name=TABLE_NAME, credential=cred)

    from azure.identity import DefaultAzureCredential

    tenant = os.getenv("AZURE_TENANT_ID", "").strip() or None
    cred = DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
        **({"interactive_browser_tenant_id": tenant} if tenant else {}),
    )
    return TableClient(endpoint=endpoint, table_name=TABLE_NAME, credential=cred)


def _coerce(entity: Any) -> dict[str, Any]:
    row = dict(entity)
    row.setdefault("runId", row.get("RowKey", ""))
    for f in NUMERIC_FIELDS:
        try:
            row[f] = float(row.get(f, 0) or 0)
        except (TypeError, ValueError):
            row[f] = 0.0
    return row


def _build_filter(
    month: str, channel: str, transport: str, persona: str,
    date_from: str, date_to: str,
) -> Optional[str]:
    clauses: list[str] = []
    if month:
        clauses.append(f"PartitionKey eq '{_esc(month)}'")
    if channel:
        clauses.append(f"channel eq '{_esc(channel)}'")
    if transport:
        clauses.append(f"transport eq '{_esc(transport)}'")
    if persona:
        clauses.append(f"persona eq '{_esc(persona)}'")
    if date_from:
        clauses.append(f"startedAt ge '{_esc(date_from)}'")
    if date_to:
        clauses.append(f"startedAt le '{_esc(date_to)}'")
    return " and ".join(clauses) if clauses else None


def _esc(value: str) -> str:
    # OData string literals escape single quotes by doubling them.
    return value.replace("'", "''")


def _query_runs(
    *, month: str = "", channel: str = "", transport: str = "",
    persona: str = "", date_from: str = "", date_to: str = "", limit: int = 1000,
) -> list[dict[str, Any]]:
    client = _get_table_client()
    if client is None:
        return []
    filt = _build_filter(month, channel, transport, persona, date_from, date_to)
    rows: list[dict[str, Any]] = []
    try:
        with client:
            it: Iterable[Any] = (
                client.query_entities(filt) if filt else client.list_entities()
            )
            for ent in it:
                rows.append(_coerce(ent))
                if len(rows) >= limit:
                    break
    except Exception:
        logger.warning("cost table query failed", exc_info=True)
        return []
    rows.sort(key=lambda r: str(r.get("startedAt", "")), reverse=True)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────
def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def bucket(key: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            k = str(r.get(key) or "—")
            agg = out.setdefault(k, {"count": 0.0, "totalUsd": 0.0})
            agg["count"] += 1
            agg["totalUsd"] += float(r.get("totalUsd", 0.0))
        return out

    total = sum(float(r.get("totalUsd", 0.0)) for r in rows)
    components = {
        "realtime": sum(float(r.get("realtimeUsd", 0.0)) for r in rows),
        "avatar": sum(float(r.get("avatarUsd", 0.0)) for r in rows),
        "vision": sum(float(r.get("visionUsd", 0.0)) for r in rows),
        "acs": sum(float(r.get("acsUsd", 0.0)) for r in rows),
    }
    by_day: dict[str, float] = {}
    for r in rows:
        day = str(r.get("startedAt", ""))[:10] or "—"
        by_day[day] = by_day.get(day, 0.0) + float(r.get("totalUsd", 0.0))
    total_seconds = sum(float(r.get("elapsedSeconds", 0.0)) for r in rows)
    return {
        "count": len(rows),
        "totalUsd": round(total, 6),
        "avgUsd": round(total / len(rows), 6) if rows else 0.0,
        "totalMinutes": round(total_seconds / 60.0, 2),
        "components": {k: round(v, 6) for k, v in components.items()},
        "byTransport": bucket("transport"),
        "byChannel": bucket("channel"),
        "byPersona": bucket("persona"),
        "byDay": {k: round(v, 6) for k, v in sorted(by_day.items())},
    }


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="costboard", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "store_configured": _get_table_client() is not None,
        "table": TABLE_NAME,
    }


@app.get("/api/runs")
def api_runs(
    month: str = Query(""),
    channel: str = Query(""),
    transport: str = Query(""),
    persona: str = Query(""),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
    limit: int = Query(1000, ge=1, le=10000),
) -> JSONResponse:
    rows = _query_runs(
        month=month, channel=channel, transport=transport, persona=persona,
        date_from=date_from, date_to=date_to, limit=limit,
    )
    return JSONResponse({"count": len(rows), "runs": rows})


@app.get("/api/summary")
def api_summary(
    month: str = Query(""),
    channel: str = Query(""),
    transport: str = Query(""),
    persona: str = Query(""),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
) -> JSONResponse:
    rows = _query_runs(
        month=month, channel=channel, transport=transport, persona=persona,
        date_from=date_from, date_to=date_to,
    )
    return JSONResponse(_summarize(rows))


@app.get("/export.csv")
def export_csv(
    month: str = Query(""),
    channel: str = Query(""),
    transport: str = Query(""),
    persona: str = Query(""),
    date_from: str = Query("", alias="from"),
    date_to: str = Query("", alias="to"),
) -> StreamingResponse:
    rows = _query_runs(
        month=month, channel=channel, transport=transport, persona=persona,
        date_from=date_from, date_to=date_to,
    )
    columns = [
        "runId", "startedAt", "endedAt", "transport", "channel", "persona",
        "model", "meetingId", "elapsedSeconds", "totalUsd",
        "realtimeUsd", "avatarUsd", "visionUsd", "acsUsd",
        "rtAudioInTokens", "rtAudioOutTokens", "rtTextInTokens", "rtTextOutTokens",
        "avatarMinutes", "visionCalls", "visionInTokens", "visionOutTokens",
        "acsMinutes", "host", "region",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="costboard-{stamp}.csv"'},
    )


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((ROOT / "static" / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("COSTBOARD_PORT", "3100"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
