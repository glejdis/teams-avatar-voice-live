"""Runtime governance for launcher-side surfaces (the ``agentgov`` seam).

The avatar drives one outbound surface on behalf of the organising recruiter
that carries candidate PII: the **interview-invite email**
(``graph_client.send_interview_invite``). This module gives that surface an
attributable ``AGENT_AUDIT`` record — DLP-scanning the message so the audit
trail shows what sensitive info types it carried — before it goes out.

It is **non-blocking by design**: the recipient is the candidate (the data
subject), so we *record* the send rather than redact it. Degrades to a no-op
(logs a warning) if ``agentgov`` or its policy files are unavailable.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("launcher.governance")

# Make the repo-root `agentgov` package importable when running the launcher.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

AGENT_ID = "lisa-voice-avatar"

try:  # pragma: no cover - import wiring
    from agentgov.security import AgentGuard
    from agentgov.security.audit import AuditEvent, emit
    from agentgov.security.dlp import DlpEngine

    _GUARD: Any = AgentGuard.from_registry(AGENT_ID)
except Exception:  # noqa: BLE001
    _GUARD = None
    AuditEvent = None  # type: ignore[assignment]
    emit = None  # type: ignore[assignment]
    DlpEngine = None  # type: ignore[assignment]
    logger.warning("agentgov unavailable; launcher governance disabled (no invite audit).", exc_info=True)


def enabled() -> bool:
    return _GUARD is not None


def audit_invite_email(
    *,
    to: str,
    subject: str,
    body_text: str = "",
    organizer_oid: str = "",
    organizer_mail: Optional[str] = None,
) -> None:
    """Emit an attributable audit event for an outbound interview invite.

    DLP-scans subject + recipient + body to populate the audit record's finding
    types, but never blocks (the recipient is the data subject). No-ops if the
    guard is disabled.
    """
    if _GUARD is None or emit is None or AuditEvent is None or DlpEngine is None:
        return
    try:
        engine = DlpEngine(_GUARD.dlp_policy)
        findings = engine.scan(f"{subject}\n{to}\n{body_text}")
        finding_types = tuple(dict.fromkeys(f.info_type for f in findings))
        event = AuditEvent(
            agent_id=AGENT_ID,
            action="invite.email",
            direction="output",
            user_oid=organizer_oid or "",
            user_mail=organizer_mail,
            user_resolved=bool(organizer_oid),
            classification=_GUARD.dlp_policy.label_for(_GUARD.sensitivity),
            dlp_verdict="audit",
            dlp_finding_types=finding_types,
            decision="allowed",
        )
        emit(event)
    except Exception:  # noqa: BLE001
        logger.debug("audit_invite_email failed", exc_info=True)
