"""Sensitive-data entitlement enforcement — Agent 365 Phase 2 (access governance).

Enforces, at the identity layer, the CoE rule that sensitive HR outputs (salary,
performance, candidate PII) are surfaced only to users who hold the required
Entra ID group. The flow:

    Phase 0 (OBO) resolves the signed-in user and a Graph token
        -> fetch the user's group memberships (/me/memberOf or groups claim)
        -> EntitlementChecker gates each sensitive data scope here.

The policy (``data_scope -> required group(s)``) is sourced from the agent
registry (``governance/agent-registry.yaml`` -> ``access.sensitive_data_groups``)
so the runtime guard and the governed inventory stay in lockstep. A scope that no
agent gates is *not* sensitive and is always allowed; a gated scope requires the
user to be in **any one** of the configured groups (any-of).

Pure standard library (YAML is imported lazily, only for the file helper), so it
is testable anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


class EntitlementError(PermissionError):
    """Raised when a user lacks the Entra group required for a sensitive scope."""

    def __init__(self, data_scope: str, required_groups: Iterable[str]) -> None:
        self.data_scope = data_scope
        self.required_groups = sorted(set(required_groups))
        super().__init__(
            f"Access to sensitive scope '{data_scope}' requires membership of one of: "
            f"{', '.join(self.required_groups) or '(none configured)'}."
        )


@dataclass(frozen=True)
class SensitiveScopePolicy:
    """Maps a sensitive ``data_scope`` to the Entra groups that may access it."""

    scope_groups: Mapping[str, frozenset[str]]

    def required_groups(self, data_scope: str) -> frozenset[str]:
        return self.scope_groups.get(data_scope, frozenset())

    def is_sensitive(self, data_scope: str) -> bool:
        return bool(self.scope_groups.get(data_scope))

    @property
    def sensitive_scopes(self) -> frozenset[str]:
        return frozenset(s for s, g in self.scope_groups.items() if g)


def _normalize(values: Iterable[str]) -> set[str]:
    return {str(v).strip().lower() for v in values if str(v).strip()}


class EntitlementChecker:
    """Decides whether a signed-in user may access a sensitive data scope."""

    def __init__(self, policy: SensitiveScopePolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> SensitiveScopePolicy:
        return self._policy

    def is_allowed(self, data_scope: str, user_group_ids: Iterable[str]) -> bool:
        required = self._policy.required_groups(data_scope)
        if not required:
            return True  # scope is not gated
        return bool(_normalize(required) & _normalize(user_group_ids))

    def assert_allowed(self, data_scope: str, user_group_ids: Iterable[str]) -> None:
        if not self.is_allowed(data_scope, user_group_ids):
            raise EntitlementError(data_scope, self._policy.required_groups(data_scope))

    def filter_allowed_scopes(
        self, data_scopes: Iterable[str], user_group_ids: Iterable[str]
    ) -> list[str]:
        return [s for s in data_scopes if self.is_allowed(s, user_group_ids)]


def extract_group_ids(member_of_response: Any) -> list[str]:
    """Pull group object ids from a Graph ``/me/memberOf`` response (or a list).

    Tolerates either the raw Graph payload (``{"value": [{"id": ...}, ...]}``) or
    an already-extracted list of ids / dicts.
    """
    if isinstance(member_of_response, dict):
        items = member_of_response.get("value", [])
    elif isinstance(member_of_response, list):
        items = member_of_response
    else:
        return []
    ids: list[str] = []
    for item in items:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


def load_policy_from_registry(registry: Mapping[str, Any]) -> SensitiveScopePolicy:
    """Build a :class:`SensitiveScopePolicy` from a parsed agent registry dict.

    Reads each agent's ``access.sensitive_data_groups`` (``data_scope -> [group]``)
    and unions the required groups per scope across all agents (any-of).
    """
    accumulated: dict[str, set[str]] = {}
    for agent in registry.get("agents", []) or []:
        if not isinstance(agent, dict):
            continue
        access = agent.get("access")
        if not isinstance(access, dict):
            continue
        mapping = access.get("sensitive_data_groups")
        if not isinstance(mapping, dict):
            continue
        for data_scope, groups in mapping.items():
            if not isinstance(groups, (list, tuple)):
                continue
            bucket = accumulated.setdefault(str(data_scope), set())
            bucket.update(str(g).strip() for g in groups if str(g).strip())
    return SensitiveScopePolicy(
        scope_groups={scope: frozenset(groups) for scope, groups in accumulated.items()}
    )


def load_policy_from_registry_file(path: Any) -> SensitiveScopePolicy:
    """Convenience loader that parses a registry YAML file (lazy PyYAML import)."""
    from pathlib import Path

    import yaml  # lazy

    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return load_policy_from_registry(data)
