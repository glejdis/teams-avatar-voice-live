"""Optional FastAPI wrapper around :mod:`launcher.cli`.

Exposes the same end-to-end ``schedule`` flow as the CLI, but over HTTP
so you can ``curl`` it from a button in another app (or host the
launcher as a tiny sidecar service)::

    pip install "teams_avatar_voice_live[web]"
    uvicorn launcher.web:app --host 0.0.0.0 --port 8080

    curl -X POST http://localhost:8080/api/schedule \\
        -H "Content-Type: application/json" \\
        -d '{"to": "alice@example.com", "start": "+10",
             "subject": "Demo"}'

This module is intentionally minimal — for anything richer (auth,
rate-limits, persistence) wrap it in your own FastAPI app and import
:func:`schedule_meeting`.
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - import-time guard
    raise ImportError(
        "FastAPI is not installed. Install with: "
        "pip install 'teams_avatar_voice_live[web]'"
    ) from exc

from . import bot_dispatcher, graph_client
from .cli import _parse_start

logger = logging.getLogger("launcher.web")

app = FastAPI(
    title="teams_avatar_voice_live launcher",
    version="0.1.3",
    description="Create Teams meeting + email invite + dispatch avatar bot.",
)


class ScheduleRequest(BaseModel):
    to: str = Field(..., description="Invitee email address")
    start: str = Field("+5", description="'+N' minutes from now, or ISO-8601")
    duration_mins: int = Field(30, ge=1, le=480)
    subject: str = Field("Interview with the AI avatar")
    name: str = Field("Guest")
    position: str = Field("Open Position")
    lang: str = Field("en", pattern="^(en|de)$")
    mode: Optional[str] = Field(None, description="graph_bot | browser_webrtc")
    bot_display_name: Optional[str] = None
    skip_email: bool = False


class ScheduleResponse(BaseModel):
    ok: bool
    join_url: str
    meeting_id: Optional[str] = None
    email: Optional[dict] = None
    dispatch: dict


def schedule_meeting(req: ScheduleRequest) -> ScheduleResponse:
    """Pure-function form of the ``/api/schedule`` endpoint (importable)."""
    start_minutes = _parse_start(req.start)
    meeting = graph_client.create_teams_meeting(
        subject=req.subject,
        start_minutes_from_now=start_minutes,
        duration_minutes=req.duration_mins,
    )
    join_url = meeting["joinUrl"]

    email_result = None
    if not req.skip_email:
        email_result = graph_client.send_interview_invite(
            candidate_email=req.to,
            candidate_name=req.name,
            position=req.position,
            join_url=join_url,
            lang=req.lang,
        )

    dispatch_result = bot_dispatcher.dispatch(
        join_url,
        mode=req.mode,
        display_name=req.bot_display_name,
        session_id=meeting.get("meetingId", ""),
        email_sent_to=req.to,
        position=req.position,
        lang=req.lang,
    )

    return ScheduleResponse(
        ok=True,
        join_url=join_url,
        meeting_id=meeting.get("meetingId"),
        email=email_result,
        dispatch=dispatch_result,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True, "graph_configured": graph_client.graph_configured()}


@app.post("/api/schedule", response_model=ScheduleResponse)
def api_schedule(req: ScheduleRequest) -> ScheduleResponse:
    try:
        return schedule_meeting(req)
    except SystemExit as exc:  # raised by _parse_start on bad input
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("schedule failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
