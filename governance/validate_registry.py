"""Validate the Agent Registry against Agent 365 governance policy.

The registry (``governance/agent-registry.yaml``) is the system of record for
every governed agent identity. This validator turns the HR AI Center of
Excellence's manual review checklist into **executable, CI-enforceable rules**,
which is the whole point of Phase 1: governance moves from "trust the reviewer"
to "enforced on every change".

Dependencies: PyYAML only (pure-Python rule checks — no ``jsonschema`` needed),
so it runs anywhere CI runs.

Usage::

    python governance/validate_registry.py                 # validate default registry
    python governance/validate_registry.py path/to/reg.yaml
    # exit code 0 = pass, 1 = governance violations, 2 = bad input
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_REGISTRY = Path(__file__).resolve().parent / "agent-registry.yaml"

_ID_RE = "abcdefghijklmnopqrstuvwxyz0123456789-"
_ALLOWED_SENSITIVITY = {"public", "internal", "confidential", "restricted"}
_ALLOWED_LIFECYCLE = {"proposed", "active", "retired"}
_ALLOWED_IDENTITY_TYPE = {"user-assigned-managed-identity", "entra-agent-id"}
_ALLOWED_IDENTITY_STATUS = {"planned", "provisioned", "retired"}
_ALLOWED_CODETERMINATION = {"required", "obtained", "not_required"}

# Over-broad Graph permissions that defeat least-privilege governance.
_FORBIDDEN_SCOPES = {".default", "directory.readwrite.all", "*"}

_REQUIRED_AGENT_FIELDS = (
    "id",
    "display_name",
    "app",
    "owner",
    "purpose",
    "data",
    "entra",
    "least_privilege",
    "lifecycle",
    "human_oversight",
)
_REQUIRED_DATA_FIELDS = ("personal_data", "special_category", "employment_decision", "sensitivity")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    agent_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, agent_id: str, message: str) -> None:
        self.errors.append(f"[{agent_id}] {message}")


def load_registry(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError("Registry root must be a mapping.")
    return data


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_registry(data: dict[str, Any]) -> ValidationResult:
    result = ValidationResult()
    agents = data.get("agents")
    if not isinstance(agents, list) or not agents:
        result.errors.append("[registry] 'agents' must be a non-empty list.")
        return result

    seen_ids: set[str] = set()
    seen_identities: set[str] = set()

    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    role_catalogue = set(defaults.get("azure_role_catalogue") or [])

    for index, agent in enumerate(agents):
        agent_id = agent.get("id") if isinstance(agent, dict) else None
        label = agent_id if _is_nonempty_str(agent_id) else f"index {index}"

        if not isinstance(agent, dict):
            result.add(str(label), "agent entry must be a mapping.")
            continue

        # Required fields present.
        for required in _REQUIRED_AGENT_FIELDS:
            if required not in agent or agent.get(required) in (None, ""):
                result.add(str(label), f"missing required field '{required}'.")

        # id format + uniqueness.
        if _is_nonempty_str(agent_id):
            if any(ch not in _ID_RE for ch in agent_id):
                result.add(agent_id, "id must match ^[a-z0-9-]+$.")
            if agent_id in seen_ids:
                result.add(agent_id, "duplicate agent id.")
            seen_ids.add(agent_id)

        result.agent_count += 1

        _validate_data_block(result, str(label), agent)
        _validate_entra_block(result, str(label), agent, seen_identities)
        _validate_least_privilege(result, str(label), agent)
        _validate_lifecycle(result, str(label), agent)
        _validate_governance_gates(result, str(label), agent)
        _validate_azure_roles(result, str(label), agent, role_catalogue)
        _validate_access(result, str(label), agent)

    return result


def _validate_data_block(result: ValidationResult, label: str, agent: dict) -> None:
    data = agent.get("data")
    if not isinstance(data, dict):
        result.add(label, "'data' must be a mapping.")
        return
    for required in _REQUIRED_DATA_FIELDS:
        if required not in data:
            result.add(label, f"data.{required} is required.")
    for flag in ("personal_data", "special_category", "employment_decision"):
        if flag in data and not isinstance(data[flag], bool):
            result.add(label, f"data.{flag} must be a boolean.")
    sensitivity = data.get("sensitivity")
    if sensitivity is not None and sensitivity not in _ALLOWED_SENSITIVITY:
        result.add(
            label,
            f"data.sensitivity '{sensitivity}' not in {sorted(_ALLOWED_SENSITIVITY)}.",
        )


def _validate_entra_block(
    result: ValidationResult, label: str, agent: dict, seen_identities: set[str]
) -> None:
    entra = agent.get("entra")
    if not isinstance(entra, dict):
        result.add(label, "'entra' must be a mapping.")
        return
    identity_name = entra.get("identity_name")
    if not _is_nonempty_str(identity_name):
        result.add(label, "entra.identity_name is required (the backing Entra identity).")
    else:
        if identity_name in seen_identities:
            result.add(label, f"entra.identity_name '{identity_name}' is not unique.")
        seen_identities.add(identity_name)
    if entra.get("identity_type") not in _ALLOWED_IDENTITY_TYPE:
        result.add(
            label,
            f"entra.identity_type must be one of {sorted(_ALLOWED_IDENTITY_TYPE)}.",
        )
    if entra.get("status") not in _ALLOWED_IDENTITY_STATUS:
        result.add(
            label,
            f"entra.status must be one of {sorted(_ALLOWED_IDENTITY_STATUS)}.",
        )


def _validate_least_privilege(result: ValidationResult, label: str, agent: dict) -> None:
    lp = agent.get("least_privilege")
    if not isinstance(lp, dict):
        result.add(label, "'least_privilege' must be a mapping.")
        return
    scopes = lp.get("graph_scopes", [])
    if not isinstance(scopes, list):
        result.add(label, "least_privilege.graph_scopes must be a list.")
        return
    for scope in scopes:
        if not _is_nonempty_str(scope):
            result.add(label, "least_privilege.graph_scopes entries must be non-empty strings.")
            continue
        if scope.strip().lower() in _FORBIDDEN_SCOPES:
            result.add(
                label,
                f"over-broad Graph scope '{scope}' defeats least privilege "
                "(no .default / wildcard / Directory.ReadWrite.All).",
            )


def _validate_lifecycle(result: ValidationResult, label: str, agent: dict) -> None:
    lifecycle = agent.get("lifecycle")
    if lifecycle is not None and lifecycle not in _ALLOWED_LIFECYCLE:
        result.add(label, f"lifecycle '{lifecycle}' not in {sorted(_ALLOWED_LIFECYCLE)}.")
    co = agent.get("co_determination")
    if co is not None and co not in _ALLOWED_CODETERMINATION:
        result.add(label, f"co_determination '{co}' not in {sorted(_ALLOWED_CODETERMINATION)}.")


def _validate_governance_gates(result: ValidationResult, label: str, agent: dict) -> None:
    """The DSGVO / BetrVG / human-oversight gates from the CoE checklist."""
    data = agent.get("data") if isinstance(agent.get("data"), dict) else {}

    # DSGVO: any agent touching personal data must name an owner + human oversight.
    if data.get("personal_data") is True:
        if not _is_nonempty_str(agent.get("owner")):
            result.add(label, "processes personal data but has no 'owner' (DSGVO accountability).")
        if not _is_nonempty_str(agent.get("human_oversight")):
            result.add(label, "processes personal data but declares no 'human_oversight'.")

    # BetrVG §87: an agent that influences employment decisions needs
    # co-determination obtained and explicit human oversight.
    if data.get("employment_decision") is True:
        if agent.get("co_determination") != "obtained":
            result.add(
                label,
                "influences employment decisions but co_determination is not "
                "'obtained' (BetrVG §87 co-determination required before go-live).",
            )
        if not _is_nonempty_str(agent.get("human_oversight")):
            result.add(label, "influences employment decisions but declares no 'human_oversight'.")


def _validate_azure_roles(result: ValidationResult, label: str, agent: dict, catalogue: set) -> None:
    """Phase 2: Azure roles must come from the approved least-privilege catalogue."""
    lp = agent.get("least_privilege")
    if not isinstance(lp, dict):
        return
    roles = lp.get("azure_roles") or []
    if not isinstance(roles, list):
        result.add(label, "least_privilege.azure_roles must be a list.")
        return
    if catalogue:
        for role in roles:
            if role not in catalogue:
                result.add(
                    label,
                    f"azure_role '{role}' is not in defaults.azure_role_catalogue "
                    "(least-privilege role catalogue).",
                )


def _validate_access(result: ValidationResult, label: str, agent: dict) -> None:
    """Phase 2: Conditional Access reference + sensitive-data Entra-group gating."""
    access = agent.get("access")
    data = agent.get("data") if isinstance(agent.get("data"), dict) else {}
    lp = agent.get("least_privilege") if isinstance(agent.get("least_privilege"), dict) else {}
    declared_scopes = set(lp.get("data_scopes") or [])

    # Special-category / restricted data must be gated behind an Entra group.
    requires_gate = data.get("special_category") is True or data.get("sensitivity") == "restricted"

    if access is None:
        if requires_gate:
            result.add(
                label,
                "special-category/restricted data requires an "
                "'access.sensitive_data_groups' Entra-group gate.",
            )
        return
    if not isinstance(access, dict):
        result.add(label, "'access' must be a mapping.")
        return

    ca = access.get("conditional_access")
    if ca is not None and not _is_nonempty_str(ca):
        result.add(label, "access.conditional_access must be a non-empty policy reference.")

    sdg = access.get("sensitive_data_groups")
    if sdg is not None:
        if not isinstance(sdg, dict):
            result.add(
                label,
                "access.sensitive_data_groups must be a mapping of data_scope -> [group].",
            )
        else:
            for scope, groups in sdg.items():
                if declared_scopes and scope not in declared_scopes:
                    result.add(
                        label,
                        f"sensitive_data_groups scope '{scope}' is not in "
                        "least_privilege.data_scopes.",
                    )
                if not isinstance(groups, (list, tuple)) or not groups:
                    result.add(
                        label,
                        f"sensitive_data_groups['{scope}'] must be a non-empty list of groups.",
                    )
                elif any(not _is_nonempty_str(g) for g in groups):
                    result.add(
                        label,
                        f"sensitive_data_groups['{scope}'] contains an empty group id.",
                    )

    if requires_gate and not (isinstance(sdg, dict) and sdg):
        result.add(
            label,
            "special-category/restricted data requires at least one "
            "sensitive_data_groups entry.",
        )


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_REGISTRY
    if not path.exists():
        print(f"ERROR: registry not found: {path}", file=sys.stderr)
        return 2
    try:
        data = load_registry(path)
    except (yaml.YAMLError, ValueError) as exc:
        print(f"ERROR: could not parse {path}: {exc}", file=sys.stderr)
        return 2

    result = validate_registry(data)
    if result.ok:
        print(f"OK: {result.agent_count} agent(s) valid in {path.name}.")
        return 0

    print(f"FAILED: {len(result.errors)} governance violation(s) in {path.name}:", file=sys.stderr)
    for error in result.errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
