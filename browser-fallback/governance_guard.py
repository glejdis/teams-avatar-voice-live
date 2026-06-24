"""Runtime governance for the browser-fallback avatar (the ``agentgov`` seam).

Wires the same ``AgentGuard`` the production hosted agent uses into the local
browser WebRTC path, so on every turn:

- inbound candidate / operator text is screened for prompt-injection
  (Defender-style) before it reaches the model, and
- the avatar's response transcript is DLP-scanned + redacted before it is
  persisted,

and **every** turn emits an attributable ``AGENT_AUDIT`` event keyed by
``(user, agent, action)``.

The browser path serves an ACS *guest* (not an authenticated Entra user), so the
identity is best-effort and ``resolved=False`` — itself a governance signal in
the audit trail. Runtime output uses DLP **redaction** (per the agent's declared
sensitivity); the Entra-group *entitlement* gate is a separate concern for the
recruiter-facing transcript-retrieval surface and is covered by the unit tests.

Degrades to a no-op (logs a warning) if ``agentgov`` or its policy files are
unavailable, so the dev server always starts.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("browser-fallback.governance")

# Make the repo-root `agentgov` package importable when running `python app.py`
# from the browser-fallback/ directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

AGENT_ID = "lisa-voice-avatar"

try:  # pragma: no cover - import wiring
    from agentgov.security import AgentGuard
    from agentgov.security.pipeline import guard_output

    _GUARD: Any = AgentGuard.from_registry(AGENT_ID)
    logger.info(
        "agentgov AgentGuard loaded for %s (sensitivity=%s)", AGENT_ID, _GUARD.sensitivity
    )
except Exception:  # noqa: BLE001
    _GUARD = None
    guard_output = None  # type: ignore[assignment]
    logger.warning(
        "agentgov unavailable; governance guard disabled (no inline DLP / audit).",
        exc_info=True,
    )


class GuestIdentity:
    """Best-effort identity for an ACS guest (unauthenticated browser path)."""

    def __init__(self, candidate_id: str = "", candidate_name: str = "") -> None:
        self.oid = candidate_id or ""
        self.mail = None
        # A browser ACS guest is NOT an authenticated Entra user.
        self.resolved = False
        self.label = candidate_name or "guest"


def guest_identity(candidate_id: str = "", candidate_name: str = "") -> GuestIdentity:
    return GuestIdentity(candidate_id, candidate_name)


def enabled() -> bool:
    return _GUARD is not None


def screen_user_input(text: str, identity: Any, *, action: str = "interview.turn") -> tuple[bool, str]:
    """Screen inbound user/operator text for prompt-injection *before* the model.

    Emits an audit event. Returns ``(allowed, safe_text)``; when blocked the
    caller must not forward the text. No-ops (allows) if the guard is disabled.
    """
    if _GUARD is None or not text.strip():
        return True, text
    try:
        res = _GUARD.screen_input(text, identity, action=action)
        return res.allowed, (text if res.allowed else "")
    except Exception:  # noqa: BLE001
        logger.warning("screen_user_input failed; allowing by default.", exc_info=True)
        return True, text


def audit_user_turn(text: str, identity: Any, *, action: str = "interview.turn") -> None:
    """Audit an already-spoken candidate utterance (STT) without blocking."""
    if _GUARD is None or not text.strip():
        return
    try:
        _GUARD.screen_input(text, identity, action=action)
    except Exception:  # noqa: BLE001
        logger.debug("audit_user_turn failed", exc_info=True)


def redact_assistant_output(text: str, identity: Any, *, action: str = "interview.turn") -> str:
    """DLP-scan + audit an avatar response; return the (possibly redacted) text.

    Uses redaction per the agent's declared sensitivity (no entitlement
    escalation at runtime). No-ops if the guard is disabled.
    """
    if _GUARD is None or guard_output is None or not text.strip():
        return text
    try:
        res = guard_output(
            text,
            identity=identity,
            agent_id=AGENT_ID,
            action=action,
            sensitivity=_GUARD.sensitivity,
            policy=_GUARD.dlp_policy,
            force_block_on_findings=False,
        )
        return res.text
    except Exception:  # noqa: BLE001
        logger.warning("redact_assistant_output failed; passing through.", exc_info=True)
        return text
