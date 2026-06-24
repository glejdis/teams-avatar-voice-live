"""Generate Bicep parameters from the agent registry (governance Phase 5).

Wires the governed registry into infrastructure: emits the ``agents`` array that
``infra/modules/agent-identities.bicep`` and ``infra/modules/agent-rbac.bicep``
consume (id, identityName, azureRoles), so the deployed identities + role
assignments always match ``governance/agent-registry.yaml``.

Deterministic output so ``--check`` can guard against drift in CI.

Usage::

    python governance/generate_bicep_params.py            # (re)write the params file
    python governance/generate_bicep_params.py --check     # fail if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validate_registry import DEFAULT_REGISTRY, load_registry, validate_registry  # noqa: E402

PARAMS_PATH = _HERE.parent / "infra" / "params" / "agent365.params.json"


def build_agents_param(registry: dict) -> list[dict]:
    """Build the ``agents`` array (sorted by id for determinism)."""
    agents = []
    for agent in registry.get("agents", []) or []:
        entra = agent.get("entra") or {}
        lp = agent.get("least_privilege") or {}
        agents.append(
            {
                "id": agent.get("id", ""),
                "identityName": entra.get("identity_name", ""),
                "azureRoles": list(lp.get("azure_roles") or []),
            }
        )
    return sorted(agents, key=lambda a: a["id"])


def build_params_doc(registry: dict) -> dict:
    """An ARM parameters file: { $schema, contentVersion, parameters: { agents } }."""
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "agents": {"value": build_agents_param(registry)}
        },
    }


def render(registry: dict) -> str:
    return json.dumps(build_params_doc(registry), indent=2, sort_keys=True) + "\n"


def generate(registry_path: Path) -> str:
    data = load_registry(registry_path)
    result = validate_registry(data)
    if not result.ok:
        raise ValueError(
            "registry is invalid; fix violations before generating params:\n  - "
            + "\n  - ".join(result.errors)
        )
    return render(data)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Generate agent365 Bicep params from the registry.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv[1:])

    try:
        content = generate(Path(args.registry))
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.check:
        actual = PARAMS_PATH.read_text(encoding="utf-8") if PARAMS_PATH.exists() else None
        if actual != content:
            print(
                f"FAILED: {PARAMS_PATH.name} is stale.\n  Run: python governance/generate_bicep_params.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {PARAMS_PATH.name} is up to date.")
        return 0

    PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(content, encoding="utf-8")
    print(f"Wrote {PARAMS_PATH.relative_to(_HERE.parent)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
