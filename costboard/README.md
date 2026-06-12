# costboard

A small dashboard for **per-call cost records**. It reads the `callcosts` Azure
Table that every transport writes through `core.cost.CostSink` and shows totals,
a component/transport/persona breakdown, a runs table, and a CSV export.

```
browser-fallback ─┐
VMSS sidecar ──────┼─▶ core/cost.py CostSink ─▶ Azure Table (callcosts) ─▶ costboard
hosted-agent (opt) ┘
```

## Run locally

```powershell
cd costboard
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py      # http://localhost:3100
```

## Configuration (`COST_STORE_*`)

The board uses the **same env vars as the writer**, so a single repo-root `.env`
drives both. Auth precedence: connection string → account key → AAD
(Managed Identity / `az login`).

| Var | Purpose |
|---|---|
| `COST_STORE_ACCOUNT` | Storage account name → `https://<acct>.table.core.windows.net` |
| `COST_STORE_ENDPOINT` | Explicit table endpoint (overrides `ACCOUNT`) |
| `COST_STORE_TABLE` | Table name (default `callcosts`) |
| `COST_STORE_CONNECTION_STRING` | Full connection string (overrides AAD) |
| `COST_STORE_KEY` | Account key (used with `ACCOUNT`/`ENDPOINT`) |
| `COSTBOARD_PORT` | Listen port (default `3100`) |

When nothing is configured the UI loads and explains the empty state — it never
crashes. To use Managed Identity / `az login`, grant the identity **Storage
Table Data Contributor** on the storage account (the infra Bicep does this for
the principals listed in `costStorePrincipalIds`).

## Endpoints

- `GET /` — dashboard UI
- `GET /api/runs` — filtered runs (`month`, `transport`, `channel`, `persona`, `from`, `to`, `limit`)
- `GET /api/summary` — aggregates for the same filters
- `GET /export.csv` — CSV of the filtered runs
- `GET /healthz` — liveness + whether the store is configured
