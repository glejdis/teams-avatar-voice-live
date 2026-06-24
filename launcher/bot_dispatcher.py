"""Avatar transport dispatcher — one front door, two transports.

After a Teams meeting URL has been created (typically via
:func:`launcher.graph_client.create_teams_meeting`), call
:func:`dispatch` to hand the URL off to whichever avatar transport is
configured.

Two transports are supported:

``graph_bot`` (production)
    POSTs the join URL to the VMSS-hosted Microsoft Graph calling bot
    (``BOT_JOIN_ENDPOINT``). The bot then dials into the meeting on
    behalf of the avatar and pipes Voice Live audio + video into the
    Teams call. Internally delegates to
    :func:`launcher.graph_client.invite_bot_to_meeting`.

``browser_webrtc`` (local dev / fallback)
    Writes the meeting URL and metadata to a JSON file
    (``DEMO_LATEST_INVITE_PATH``) that the ``browser-fallback/`` web UI
    polls. An operator (or auto-join checkbox) then connects via the
    Azure Communication Services WebRTC SDK from the browser.

Mode resolution order: explicit ``mode`` argument →
``TEAMS_JOIN_MODE`` env var → ``graph_bot``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import graph_client

logger = logging.getLogger(__name__)

GRAPH_BOT_MODE = "graph_bot"
BROWSER_WEBRTC_MODE = "browser_webrtc"
SUPPORTED_MODES = {GRAPH_BOT_MODE, BROWSER_WEBRTC_MODE}

_DEFAULT_LATEST_INVITE_PATH = "browser-fallback/data/latest-invite.json"


def resolve_mode(mode: str | None = None) -> str:
    """Return the effective transport mode (validated)."""
    chosen = (mode or os.getenv("TEAMS_JOIN_MODE") or GRAPH_BOT_MODE).strip().lower()
    if chosen not in SUPPORTED_MODES:
        raise ValueError(
            f"Unsupported TEAMS_JOIN_MODE={chosen!r}. "
            f"Use one of: {sorted(SUPPORTED_MODES)}"
        )
    return chosen


def _latest_invite_path() -> Path:
    raw = os.getenv("DEMO_LATEST_INVITE_PATH", _DEFAULT_LATEST_INVITE_PATH).strip()
    path = Path(raw)
    if not path.is_absolute():
        # Resolve relative to repo root (two levels up from this file).
        path = Path(__file__).resolve().parents[1] / path
    return path


def _record_latest_invite(
    *,
    join_url: str,
    session_id: str,
    display_name: str,
    email_sent_to: str,
    extra: dict[str, Any],
) -> Path:
    """Persist the meeting record so the browser fallback can auto-fill."""
    target = _latest_invite_path()
    record: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "meeting_url": join_url,
        "session_id": session_id,
        "display_name": display_name,
        "email_sent_to": email_sent_to,
    }
    record.update({k: v for k, v in extra.items() if v is not None})

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    tmp.replace(target)
    logger.info("Recorded latest invite for browser fallback: %s", target)
    return target


def dispatch(
    join_url: str,
    *,
    mode: str | None = None,
    display_name: str | None = None,
    session_id: str = "",
    email_sent_to: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """Hand ``join_url`` off to the configured avatar transport.

    Parameters
    ----------
    join_url
        Teams ``joinWebUrl`` returned by ``create_teams_meeting``.
    mode
        ``"graph_bot"`` or ``"browser_webrtc"``. Default: env
        ``TEAMS_JOIN_MODE`` → ``graph_bot``.
    display_name
        Roster name shown in the Teams lobby. Default: env
        ``BOT_DISPLAY_NAME`` or ``"Avatar Bot"``.
    session_id
        Opaque correlation ID forwarded to the bot / browser.
    email_sent_to
        Recorded in the browser-fallback JSON for operator visibility.
    **extra
        Arbitrary metadata persisted (browser_webrtc) or forwarded
        (graph_bot) — e.g. ``position="…"``, ``lang="en"``.

    Returns
    -------
    dict
        ``{"mode": str, "status": str, "avatar_will_join": bool, "details": {...}}``.
        ``avatar_will_join`` is ``True`` only when the graph_bot dial-in was
        accepted; it is ``False`` for a failed dial-in or a browser_webrtc
        handoff (where joining is deferred to a live browser). A loud warning is
        logged and an ``avatar.join.*`` AGENT_AUDIT event emitted whenever no
        avatar will actually join, so "invite emailed, nobody joined" is visible.
    """
    if not join_url:
        raise ValueError("join_url is required")

    effective_mode = resolve_mode(mode)
    name = display_name or os.getenv("BOT_DISPLAY_NAME") or "Avatar Bot"

    if effective_mode == GRAPH_BOT_MODE:
        details = graph_client.invite_bot_to_meeting(
            join_url,
            display_name=name,
            session_id=session_id,
            **{k: v for k, v in extra.items() if isinstance(v, str)},
        )
        joined = bool(details.get("ok"))
        if not joined:
            logger.warning(
                "AVATAR WILL NOT JOIN: graph_bot dial-in did not succeed "
                "(skipped=%s error=%r). The meeting was created%s but no avatar "
                "entered it. Check BOT_JOIN_ENDPOINT and the lisa-bot health; set "
                "BOT_JOIN_REQUIRED=true to fail loudly instead of emailing a link "
                "no one joins.",
                details.get("skipped"),
                details.get("error"),
                " and the invite emailed" if email_sent_to else "",
            )
        _audit_join(effective_mode, "requested" if joined else "failed", session_id, email_sent_to)
        return {
            "mode": effective_mode,
            "status": "join_requested" if joined else "failed",
            "avatar_will_join": joined,
            "details": details,
        }

    # browser_webrtc — record the invite so the WebRTC operator page picks it up.
    target = _record_latest_invite(
        join_url=join_url,
        session_id=session_id,
        display_name=name,
        email_sent_to=email_sent_to,
        extra=extra,
    )
    logger.warning(
        "AVATAR JOIN DEFERRED TO BROWSER (mode=browser_webrtc): the avatar will "
        "NOT auto-join. An operator must open the browser-fallback page (or a live "
        "auto-join tab must be running) to actually enter the meeting. For "
        "hands-off joining set TEAMS_JOIN_MODE=graph_bot."
    )
    _audit_join(effective_mode, "deferred", session_id, email_sent_to)
    return {
        "mode": effective_mode,
        "status": "handoff_recorded",
        "avatar_will_join": False,
        "details": {"latest_invite_path": str(target)},
    }


def _audit_join(mode: str, outcome: str, session_id: str, email_sent_to: str) -> None:
    """Best-effort governance audit of the join handoff (degrade-safe)."""
    try:
        from launcher.governance import audit_join

        audit_join(mode=mode, outcome=outcome, session_id=session_id, organizer_mail=email_sent_to or None)
    except Exception:  # noqa: BLE001
        logger.debug("join governance audit failed", exc_info=True)
