import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generate_inventory import (  # noqa: E402
    HTML_PATH,
    JSON_PATH,
    build_inventory,
    generate,
    render_html,
    render_json,
)
from validate_registry import DEFAULT_REGISTRY, load_registry  # noqa: E402


def _registry(*agents):
    return {"version": 1, "agents": list(agents)}


def _agent(**over):
    base = {
        "id": "x-agent",
        "display_name": "X Agent",
        "app": "x-app",
        "runtime": "flask",
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
            "identity_name": "id-x",
            "identity_type": "user-assigned-managed-identity",
            "status": "planned",
        },
        "least_privilege": {"graph_scopes": ["User.Read"], "azure_roles": [], "data_scopes": ["d"]},
        "access": {"conditional_access": "ca-agents-baseline"},
        "sub_agents": ["a", "b"],
    }
    base.update(over)
    return base


class BuildInventoryTests(unittest.TestCase):
    def test_shipped_registry_summary(self):
        inv = build_inventory(load_registry(DEFAULT_REGISTRY))
        self.assertEqual(inv["summary"]["total_agents"], 1)
        self.assertEqual(inv["summary"]["personal_data_agents"], 1)
        # every shipped agent has full governance posture
        self.assertEqual(inv["summary"]["fully_compliant"], inv["summary"]["total_agents"])

    def test_label_map_applied(self):
        inv = build_inventory(
            _registry(_agent(data={"personal_data": True, "special_category": False,
                                   "employment_decision": False, "sensitivity": "restricted"})),
            label_map={"restricted": "Highly-Confidential-HR"},
        )
        self.assertEqual(inv["agents"][0]["data"]["label"], "Highly-Confidential-HR")

    def test_posture_flags_broad_scope(self):
        agent = _agent(least_privilege={"graph_scopes": [".default"], "azure_roles": [], "data_scopes": []})
        inv = build_inventory(_registry(agent))
        self.assertFalse(inv["agents"][0]["posture"]["least_privilege"])
        self.assertLess(inv["agents"][0]["posture_score"], inv["agents"][0]["posture_total"])

    def test_posture_requires_gate_for_special_category(self):
        agent = _agent(
            data={"personal_data": True, "special_category": True,
                  "employment_decision": False, "sensitivity": "confidential"},
            access={"conditional_access": "ca-agents-baseline"},  # no sensitive_data_groups
        )
        inv = build_inventory(_registry(agent))
        self.assertFalse(inv["agents"][0]["posture"]["data_gated"])


class RenderTests(unittest.TestCase):
    def test_html_contains_agents(self):
        registry = load_registry(DEFAULT_REGISTRY)
        html = render_html(build_inventory(registry))
        self.assertIn("Agent Governance Inventory", html)
        for agent in registry["agents"]:
            self.assertIn(agent["id"], html)

    def test_json_is_deterministic(self):
        registry = load_registry(DEFAULT_REGISTRY)
        a = render_json(build_inventory(registry))
        b = render_json(build_inventory(registry))
        self.assertEqual(a, b)


class CommittedArtifactsTests(unittest.TestCase):
    def test_committed_artifacts_match_registry(self):
        """Guards against drift — same contract as `--check` in CI."""
        html_out, json_out = generate(DEFAULT_REGISTRY)
        self.assertTrue(HTML_PATH.exists() and JSON_PATH.exists(),
                        "inventory artifacts missing; run generate_inventory.py")
        self.assertEqual(HTML_PATH.read_text(encoding="utf-8"), html_out,
                         "agent-inventory.html is stale; regenerate it")
        self.assertEqual(JSON_PATH.read_text(encoding="utf-8"), json_out,
                         "agent-inventory.json is stale; regenerate it")


class InvalidRegistryTests(unittest.TestCase):
    def test_generate_rejects_invalid_registry(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write("version: 1\nagents:\n  - id: bad\n")  # missing required fields
            path = Path(fh.name)
        try:
            with self.assertRaises(ValueError):
                generate(path)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
