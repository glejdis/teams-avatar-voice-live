"""Resolve tenant placeholders into deploy-ready artifacts (governance Phase 5).

The committed governance artifacts intentionally use logical placeholders (Entra
group names like ``grp-tva-recruiters``, a Conditional Access policy slot, the
agent identity name) so no tenant-specific ids live in source control. This tool
takes a real ``tenant-config.local.yaml`` (see ``tenant-config.example.yaml``)
and:

- **validates** that every placeholder the registry actually uses is mapped to a
  real value (``--check`` fails on anything still unset / left as the all-zero
  GUID), and
- **resolves** the registry into
  ``governance/tenant/out/agent-registry.resolved.json`` with each sensitive-data
  group name replaced by its real Entra group object id.

Both the real config (``*.local.*``) and the ``out/`` artifacts are gitignored.

Usage::

    python governance/apply_tenant_config.py --config governance/tenant/tenant-config.local.yaml
    python governance/apply_tenant_config.py --config <path> --check
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validate_registry import DEFAULT_REGISTRY, load_registry  # noqa: E402

OUT_DIR = _HERE / "tenant" / "out"
_ANGLE_PLACEHOLDER = re.compile(r"^<.*>$")
_ZERO_GUID = "00000000-0000-0000-0000-000000000000"


def is_placeholder(value: object) -> bool:
    """True if a value is unset, the example all-zero GUID, or an <angle> token."""
    if value is None:
        return True
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == _ZERO_GUID:
            return True
        if _ANGLE_PLACEHOLDER.match(stripped):
            return True
    return False


def _required_groups(registry: dict) -> set[str]:
    groups: set[str] = set()
    for agent in registry.get("agents", []) or []:
        access = agent.get("access") or {}
        for grp_list in (access.get("sensitive_data_groups") or {}).values():
            groups.update(grp_list or [])
    return groups


def _required_ca_policies(registry: dict) -> set[str]:
    policies: set[str] = set()
    for agent in registry.get("agents", []) or []:
        ca = (agent.get("access") or {}).get("conditional_access")
        if ca:
            policies.add(ca)
    return policies


def _required_identities(registry: dict) -> list[str]:
    return [
        (a.get("entra") or {}).get("identity_name", "")
        for a in registry.get("agents", []) or []
        if (a.get("entra") or {}).get("identity_name")
    ]


def find_unmapped(config: dict, registry: dict) -> list[str]:
    """Return human-readable descriptions of every still-unmapped placeholder."""
    problems: list[str] = []

    groups = config.get("groups") or {}
    for logical in sorted(_required_groups(registry)):
        if is_placeholder(groups.get(logical)):
            problems.append(f"groups['{logical}'] is not set")

    ca = config.get("conditional_access") or {}
    for policy in sorted(_required_ca_policies(registry)):
        if is_placeholder(ca.get(policy)):
            problems.append(f"conditional_access['{policy}'] is not set")

    identities = config.get("identities") or {}
    for name in _required_identities(registry):
        if is_placeholder(identities.get(name)):
            problems.append(f"identities['{name}'] is not set")

    return problems


def resolve_registry(registry: dict, config: dict) -> dict:
    """Replace each sensitive-data group name with its real Entra group id."""
    groups = config.get("groups") or {}
    resolved = copy.deepcopy(registry)
    for agent in resolved.get("agents", []) or []:
        sdg = (agent.get("access") or {}).get("sensitive_data_groups")
        if isinstance(sdg, dict):
            for scope, logical_groups in sdg.items():
                sdg[scope] = [groups.get(g, g) for g in (logical_groups or [])]
    return resolved


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Resolve tenant placeholders into deploy artifacts.")
    parser.add_argument("--config", required=True, help="path to tenant-config.local.yaml")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--check", action="store_true", help="only validate that nothing is unmapped")
    args = parser.parse_args(argv[1:])

    import yaml

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: tenant config not found: {config_path}", file=sys.stderr)
        return 2
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    registry = load_registry(Path(args.registry))

    problems = find_unmapped(config, registry)
    if problems:
        print(f"FAILED: {len(problems)} unmapped tenant value(s):", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.check:
        print("OK: all tenant placeholders are mapped.")
        return 0

    registry_resolved = resolve_registry(registry, config)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "agent-registry.resolved.json").write_text(
        json.dumps(registry_resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote resolved artifacts to {OUT_DIR.relative_to(_HERE.parent)}/ (gitignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
