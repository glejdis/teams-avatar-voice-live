"""Governed I/O pipeline — the single enforcement seam for agents (Phase 3).

An agent calls :func:`guard_input` on the user's message (Defender-style
prompt-injection screening) and :func:`guard_output` on its own response (Purview
DLP scan + redact/block), and each call emits an attributable
:class:`~agentgov.security.audit.AuditEvent`.

This is where the Phase 3 exit criterion lives: a candidate-PII leak in an agent
output whose data is ``restricted`` is **blocked** and the attempt is recorded in
the audit log keyed to ``(user, agent, action)``.

The caller supplies the resolved identity (Phase 0 OBO) as anything exposing
``oid`` / ``mail`` / ``resolved`` (e.g. messaging-endpoint ``IdentityContext`` or
``agentgov.auth.UserProfile``); a plain object/dict works too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .audit import AuditEvent, emit
from .dlp import DlpEngine, DlpVerdict
from .injection import detect_prompt_injection
from .policy import DlpPolicy


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    text: str
    event: AuditEvent

    @property
    def blocked(self) -> bool:
        return not self.allowed


def _identity_fields(identity: Any) -> tuple[str, Optional[str], bool]:
    def get(name: str) -> Any:
        if isinstance(identity, dict):
            return identity.get(name)
        return getattr(identity, name, None)

    oid = get("oid") or ""
    mail = get("mail")
    resolved = bool(get("resolved")) if get("resolved") is not None else bool(oid)
    return str(oid), (str(mail) if mail else None), resolved


def guard_input(
    text: str,
    *,
    identity: Any,
    agent_id: str,
    action: str,
    policy: DlpPolicy,
    log: Optional[logging.Logger] = None,
) -> GuardResult:
    """Screen a user message for prompt injection before it reaches the model."""
    oid, mail, resolved = _identity_fields(identity)
    injection = detect_prompt_injection(text)
    blocked = injection.should_block

    event = AuditEvent(
        agent_id=agent_id,
        action=action,
        direction="input",
        user_oid=oid,
        user_mail=mail,
        user_resolved=resolved,
        injection_detected=injection.detected,
        injection_signals=tuple(injection.signal_ids),
        decision="blocked" if blocked else "allowed",
    )
    emit(event, log=log)
    return GuardResult(allowed=not blocked, text="" if blocked else text, event=event)


def guard_output(
    text: str,
    *,
    identity: Any,
    agent_id: str,
    action: str,
    sensitivity: Optional[str],
    policy: DlpPolicy,
    data_scope: Optional[str] = None,
    force_block_on_findings: bool = False,
    log: Optional[logging.Logger] = None,
) -> GuardResult:
    """Apply DLP to an agent response before it is surfaced to the user.

    When ``force_block_on_findings`` is set (e.g. the user is not entitled to the
    data scope), any sensitive finding escalates a ``redact``/``audit`` verdict to
    a hard **block** — the content is withheld entirely, not just masked.
    """
    oid, mail, resolved = _identity_fields(identity)
    engine = DlpEngine(policy)
    result = engine.inspect(text, sensitivity)

    entitlement_block = (
        force_block_on_findings and result.has_findings and result.verdict != DlpVerdict.BLOCK
    )
    blocked = result.verdict == DlpVerdict.BLOCK or entitlement_block
    verdict_str = "block" if blocked else result.verdict.value
    out_text = "" if blocked else result.redacted_text
    block_reason = None
    if blocked:
        block_reason = "entitlement" if entitlement_block else "dlp"

    event = AuditEvent(
        agent_id=agent_id,
        action=action,
        direction="output",
        user_oid=oid,
        user_mail=mail,
        user_resolved=resolved,
        data_scope=data_scope,
        classification=policy.label_for(sensitivity),
        dlp_verdict=verdict_str,
        dlp_finding_types=tuple(result.finding_types),
        decision="blocked" if blocked else "allowed",
        block_reason=block_reason,
    )
    emit(event, log=log)
    return GuardResult(allowed=not blocked, text=out_text, event=event)
