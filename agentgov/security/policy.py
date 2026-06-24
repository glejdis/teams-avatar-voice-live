"""DLP / classification policy model + loader (Agent 365 Phase 3).

Loads ``governance/security/dlp-policy.yaml`` into a strongly-typed
:class:`DlpPolicy`. The loader is strict — a malformed policy raises rather than
silently under-protecting. PyYAML is imported lazily so :func:`load_policy`
(dict-based) stays stdlib-only for tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

_VALID_ACTIONS = {"audit", "redact", "block"}
_DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[2] / "governance" / "security"
DEFAULT_POLICY_PATH = _DEFAULT_REGISTRY_DIR / "dlp-policy.yaml"


@dataclass(frozen=True)
class InfoType:
    """A sensitive information type detector."""

    id: str
    pattern: str
    severity: str = "medium"
    description: str = ""
    _compiled: re.Pattern = field(default=None, repr=False, compare=False)

    def regex(self) -> re.Pattern:
        return self._compiled or re.compile(self.pattern)


@dataclass(frozen=True)
class DlpPolicy:
    info_types: tuple[InfoType, ...]
    actions: Mapping[str, str]
    sensitivity_labels: Mapping[str, str]
    default_action: str = "redact"

    def action_for(self, sensitivity: str | None) -> str:
        if sensitivity and sensitivity in self.actions:
            return self.actions[sensitivity]
        return self.default_action

    def label_for(self, sensitivity: str | None) -> str:
        if sensitivity and sensitivity in self.sensitivity_labels:
            return self.sensitivity_labels[sensitivity]
        return "Unclassified"


def load_policy(data: Mapping[str, Any]) -> DlpPolicy:
    if not isinstance(data, Mapping):
        raise ValueError("DLP policy root must be a mapping.")

    raw_types = data.get("info_types")
    if not isinstance(raw_types, list) or not raw_types:
        raise ValueError("DLP policy must define a non-empty 'info_types' list.")

    info_types: list[InfoType] = []
    for entry in raw_types:
        if not isinstance(entry, Mapping) or not entry.get("id") or not entry.get("pattern"):
            raise ValueError(f"Invalid info_type entry (needs id + pattern): {entry!r}")
        try:
            compiled = re.compile(str(entry["pattern"]))
        except re.error as exc:
            raise ValueError(f"info_type '{entry['id']}' has an invalid regex: {exc}") from exc
        info_types.append(
            InfoType(
                id=str(entry["id"]),
                pattern=str(entry["pattern"]),
                severity=str(entry.get("severity", "medium")),
                description=str(entry.get("description", "")),
                _compiled=compiled,
            )
        )

    actions = data.get("actions") or {}
    if not isinstance(actions, Mapping):
        raise ValueError("DLP policy 'actions' must be a mapping.")
    for sensitivity, action in actions.items():
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"actions['{sensitivity}'] = '{action}' is invalid "
                f"(must be one of {sorted(_VALID_ACTIONS)})."
            )

    default_action = str(data.get("default_action", "redact"))
    if default_action not in _VALID_ACTIONS:
        raise ValueError(f"default_action '{default_action}' is invalid.")

    labels = data.get("sensitivity_labels") or {}
    if not isinstance(labels, Mapping):
        raise ValueError("DLP policy 'sensitivity_labels' must be a mapping.")

    return DlpPolicy(
        info_types=tuple(info_types),
        actions=dict(actions),
        sensitivity_labels=dict(labels),
        default_action=default_action,
    )


def load_policy_file(path: Any = DEFAULT_POLICY_PATH) -> DlpPolicy:
    import yaml  # lazy

    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return load_policy(data)
