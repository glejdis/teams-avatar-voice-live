"""AgentGuard — the one-call governance seam an agent wires at its I/O boundary.

Bundles the Phase 2 + 3 controls, configured for a specific registered agent
straight from ``governance/agent-registry.yaml`` + ``governance/security/
dlp-policy.yaml``:

    guard = AgentGuard.from_registry("hr-support-orchestrator")

    checked = guard.screen_input(user_text, identity, action="chat.message")
    if checked.blocked:                       # prompt-injection / jailbreak
        return "I can't process that request."

    answer = run_the_agent(user_text)

    out = guard.screen_output(answer, identity, action="chat.message",
                              user_groups=user_groups)   # DLP + entitlement
    return out.text                            # redacted, or "" if blocked

Every call emits an attributable ``AGENT_AUDIT`` event. ``screen_output`` blocks
(not just redacts) sensitive findings when the user is **not entitled** to the
agent's gated data — joining the entitlement gate (Phase 2) to DLP (Phase 3).

Reads the governance YAML by path (no dependency on the governance tooling
package); falls back to a permissive-but-auditing guard if the files are absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .dlp import DlpPolicy
from .entitlements_bridge import build_entitlement_checker
from .pipeline import GuardResult, guard_input, guard_output
from .policy import DEFAULT_POLICY_PATH, load_policy, load_policy_file

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = _REPO_ROOT / "governance" / "agent-registry.yaml"

# Minimal, safe DLP policy used if the policy file is missing (audit-only).
_FALLBACK_POLICY = {
    "sensitivity_labels": {},
    "actions": {},
    "default_action": "audit",
    "info_types": [
        {"id": "email", "pattern": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"},
    ],
}


def _load_registry_dict(path: Path) -> dict:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        logger.warning("Could not load agent registry at %s; entitlements disabled.", path, exc_info=True)
        return {}


@dataclass
class AgentGuard:
    agent_id: str
    sensitivity: Optional[str]
    sensitive_scopes: tuple[str, ...]
    dlp_policy: DlpPolicy
    entitlements: Any  # EntitlementChecker
    _logger: logging.Logger = field(default=logger, repr=False)

    @classmethod
    def from_registry(
        cls,
        agent_id: str,
        *,
        registry_path: Path = DEFAULT_REGISTRY_PATH,
        dlp_policy_path: Path = DEFAULT_POLICY_PATH,
    ) -> "AgentGuard":
        registry = _load_registry_dict(Path(registry_path))
        agent = next(
            (a for a in registry.get("agents", []) or [] if a.get("id") == agent_id),
            {},
        )
        data = agent.get("data") or {}
        access = agent.get("access") or {}
        sensitive_scopes = tuple((access.get("sensitive_data_groups") or {}).keys())

        try:
            dlp_policy = load_policy_file(Path(dlp_policy_path))
        except Exception:  # noqa: BLE001
            logger.warning("DLP policy unavailable; using audit-only fallback.", exc_info=True)
            dlp_policy = load_policy(_FALLBACK_POLICY)

        return cls(
            agent_id=agent_id,
            sensitivity=data.get("sensitivity"),
            sensitive_scopes=sensitive_scopes,
            dlp_policy=dlp_policy,
            entitlements=build_entitlement_checker(registry),
        )

    # ── input ────────────────────────────────────────────────────────────────

    def screen_input(self, text: str, identity: Any, *, action: str) -> GuardResult:
        return guard_input(
            text, identity=identity, agent_id=self.agent_id, action=action,
            policy=self.dlp_policy, log=self._logger,
        )

    # ── entitlement ──────────────────────────────────────────────────────────

    def is_entitled(self, user_groups: Iterable[str]) -> bool:
        """True if the user may access ALL of this agent's gated data scopes."""
        groups = list(user_groups)
        return all(self.entitlements.is_allowed(scope, groups) for scope in self.sensitive_scopes)

    # ── output ───────────────────────────────────────────────────────────────

    def screen_output(
        self,
        text: str,
        identity: Any,
        *,
        action: str,
        user_groups: Iterable[str] = (),
        data_scope: Optional[str] = None,
    ) -> GuardResult:
        force_block = bool(self.sensitive_scopes) and not self.is_entitled(user_groups)
        return guard_output(
            text, identity=identity, agent_id=self.agent_id, action=action,
            sensitivity=self.sensitivity, policy=self.dlp_policy,
            data_scope=data_scope, force_block_on_findings=force_block,
            log=self._logger,
        )
