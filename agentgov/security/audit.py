"""Structured, attributable audit event — Agent 365 Phase 3.

Every governed agent action emits one :class:`AuditEvent` keyed by
``(user oid, agent id, action)`` and carrying the Purview sensitivity label, the
DLP verdict, and any Defender (injection) signals. This is the evidence the CoE
needs for DSGVO accountability, EU AI Act logging, and BetrVG transparency —
attributable to a real person and a registered agent, never the app identity.

The record is a flat JSON object so it ships cleanly to Application Insights /
Log Analytics / Purview audit. ``emit`` writes a single ``AGENT_AUDIT`` log line.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuditEvent:
    agent_id: str
    action: str
    user_oid: str = ""
    user_mail: Optional[str] = None
    user_resolved: bool = False
    data_scope: Optional[str] = None
    classification: str = "Unclassified"
    dlp_verdict: str = "allow"
    dlp_finding_types: tuple[str, ...] = ()
    injection_detected: bool = False
    injection_signals: tuple[str, ...] = ()
    decision: str = "allowed"  # allowed | blocked
    block_reason: Optional[str] = None  # e.g. "dlp" | "entitlement" | "injection"
    direction: str = "output"  # input | output
    timestamp: str = field(default_factory=_utcnow_iso)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> dict[str, Any]:
        """Flat JSON matching the ``AgentAudit_CL`` table (infra/modules/audit-sink.bicep).

        Flat keys (not nested objects) so Log Analytics derives the columns the
        Sentinel rule queries — ``agentId_s``, ``userOid_g``, ``dlpVerdict_s``,
        ``injectionDetected_b``, ``decision_s`` … — instead of a single nested blob.
        """
        return {
            "event": "agent.audit",
            "timestamp": self.timestamp,
            "correlationId": self.correlation_id,
            "agentId": self.agent_id,
            "action": self.action,
            "direction": self.direction,
            "userOid": self.user_oid or None,
            "userMail": self.user_mail,
            "userResolved": self.user_resolved,
            "dataScope": self.data_scope,
            "classification": self.classification,
            "dlpVerdict": self.dlp_verdict,
            "dlpFindingTypes": list(self.dlp_finding_types),
            "injectionDetected": self.injection_detected,
            "injectionSignals": list(self.injection_signals),
            "decision": self.decision,
            "blockReason": self.block_reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def emit(event: AuditEvent, *, log: Optional[logging.Logger] = None) -> None:
    (log or logging.getLogger(__name__)).info("AGENT_AUDIT %s", event.to_json())
