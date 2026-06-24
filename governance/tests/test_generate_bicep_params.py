import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generate_bicep_params import (  # noqa: E402
    PARAMS_PATH,
    build_agents_param,
    generate,
    render,
)
from validate_registry import DEFAULT_REGISTRY, load_registry  # noqa: E402


def _registry(*agents):
    return {"version": 1, "agents": list(agents)}


def _agent(**over):
    base = {
        "id": "a-agent",
        "display_name": "A",
        "app": "a-app",
        "owner": "CoE",
        "purpose": "p",
        "human_oversight": "human reviews",
        "lifecycle": "active",
        "data": {
            "personal_data": True,
            "special_category": False,
            "employment_decision": False,
            "sensitivity": "confidential",
        },
        "entra": {
            "identity_name": "id-a",
            "identity_type": "user-assigned-managed-identity",
            "status": "planned",
        },
        "least_privilege": {"graph_scopes": ["User.Read"], "azure_roles": ["Azure AI User"], "data_scopes": []},
        "sub_agents": [],
    }
    base.update(over)
    return base


class BuildAgentsParamTests(unittest.TestCase):
    def test_maps_registry_fields(self):
        agents = build_agents_param(_registry(_agent()))
        self.assertEqual(agents, [{"id": "a-agent", "identityName": "id-a", "azureRoles": ["Azure AI User"]}])

    def test_sorted_by_id(self):
        agents = build_agents_param(
            _registry(_agent(id="z", entra={"identity_name": "id-z", "identity_type": "user-assigned-managed-identity", "status": "planned"}),
                      _agent(id="a"))
        )
        self.assertEqual([a["id"] for a in agents], ["a", "z"])

    def test_shipped_registry_has_lisa(self):
        agents = build_agents_param(load_registry(DEFAULT_REGISTRY))
        ids = [a["id"] for a in agents]
        self.assertIn("lisa-voice-avatar", ids)


class GenerateTests(unittest.TestCase):
    def test_generate_is_deterministic(self):
        a = generate(DEFAULT_REGISTRY)
        b = generate(DEFAULT_REGISTRY)
        self.assertEqual(a, b)

    def test_committed_params_match_registry(self):
        """Same contract as `--check` in CI."""
        expected = render(load_registry(DEFAULT_REGISTRY))
        self.assertTrue(PARAMS_PATH.exists(), "params file missing; run generate_bicep_params.py")
        self.assertEqual(PARAMS_PATH.read_text(encoding="utf-8"), expected,
                         "agent365.params.json is stale; regenerate it")

    def test_generate_rejects_invalid_registry(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write("version: 1\nagents:\n  - id: bad\n")
            path = Path(fh.name)
        try:
            with self.assertRaises(ValueError):
                generate(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
