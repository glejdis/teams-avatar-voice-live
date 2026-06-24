"""Runtime security & compliance for agents — Agent 365 Phase 3.

Inline data protection that mirrors what Microsoft Purview DLP + sensitivity
labels and Microsoft Defender enforce centrally, applied by agents on every turn:

- :mod:`dlp`            — detect + redact + block sensitive info in agent I/O
- :mod:`classification` — map registry sensitivity to Purview labels
- :mod:`injection`      — Defender-style prompt-injection / jailbreak detection
- :mod:`audit`          — structured audit event keyed by (user, agent, action)
- :mod:`pipeline`       — guard_input / guard_output that tie it together

Policy source of truth: ``governance/security/dlp-policy.yaml``.
"""

from __future__ import annotations

from .audit import AuditEvent, emit
from .classification import label_for_sensitivity
from .dlp import DlpEngine, DlpFinding, DlpVerdict
from .injection import InjectionSignal, detect_prompt_injection
from .integration import AgentGuard
from .pipeline import GuardResult, guard_input, guard_output
from .policy import DlpPolicy, load_policy, load_policy_file

__all__ = [
    "AuditEvent",
    "emit",
    "label_for_sensitivity",
    "DlpEngine",
    "DlpFinding",
    "DlpVerdict",
    "InjectionSignal",
    "detect_prompt_injection",
    "AgentGuard",
    "GuardResult",
    "guard_input",
    "guard_output",
    "DlpPolicy",
    "load_policy",
    "load_policy_file",
]
