import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_registry import (  # noqa: E402
    DEFAULT_REGISTRY,
    load_registry,
    validate_registry,
)


def _valid_agent() -> dict:
    return {
        "id": "sample-agent",
        "display_name": "Sample Agent",
        "app": "sample-app",
        "owner": "HR AI Center of Excellence",
        "purpose": "Does a governed thing.",
        "human_oversight": "Human reviews everything.",
        "co_determination": "not_required",
        "lifecycle": "active",
        "data": {
            "personal_data": True,
            "special_category": False,
            "employment_decision": False,
            "sensitivity": "confidential",
        },
        "entra": {
            "identity_name": "id-aldihr-sample",
            "identity_type": "user-assigned-managed-identity",
            "status": "planned",
        },
        "least_privilege": {
            "graph_scopes": ["User.Read"],
            "azure_roles": ["Azure AI User"],
            "data_scopes": ["sample-data"],
        },
        "sub_agents": [],
    }


def _registry(*agents: dict) -> dict:
    return {"version": 1, "agents": list(agents)}


class RealRegistryTests(unittest.TestCase):
    def test_shipped_registry_is_valid(self):
        data = load_registry(DEFAULT_REGISTRY)
        result = validate_registry(data)
        self.assertTrue(result.ok, msg="\n".join(result.errors))
        self.assertGreaterEqual(result.agent_count, 1)


class HappyPathTests(unittest.TestCase):
    def test_minimal_valid_agent_passes(self):
        self.assertTrue(validate_registry(_registry(_valid_agent())).ok)


class StructuralRuleTests(unittest.TestCase):
    def test_empty_agents_list_fails(self):
        self.assertFalse(validate_registry(_registry()).ok)

    def test_missing_required_field_fails(self):
        agent = _valid_agent()
        del agent["purpose"]
        result = validate_registry(_registry(agent))
        self.assertFalse(result.ok)
        self.assertTrue(any("purpose" in e for e in result.errors))

    def test_bad_id_pattern_fails(self):
        agent = _valid_agent()
        agent["id"] = "Bad_ID"
        result = validate_registry(_registry(agent))
        self.assertTrue(any("^[a-z0-9-]+$" in e for e in result.errors))

    def test_duplicate_id_fails(self):
        a, b = _valid_agent(), _valid_agent()
        b["entra"]["identity_name"] = "id-aldihr-other"
        result = validate_registry(_registry(a, b))
        self.assertTrue(any("duplicate agent id" in e for e in result.errors))

    def test_duplicate_identity_name_fails(self):
        a, b = _valid_agent(), _valid_agent()
        b["id"] = "sample-agent-2"  # same identity_name as a
        result = validate_registry(_registry(a, b))
        self.assertTrue(any("not unique" in e for e in result.errors))

    def test_invalid_sensitivity_fails(self):
        agent = _valid_agent()
        agent["data"]["sensitivity"] = "top-secret"
        self.assertFalse(validate_registry(_registry(agent)).ok)

    def test_invalid_identity_type_fails(self):
        agent = _valid_agent()
        agent["entra"]["identity_type"] = "service-principal"
        self.assertFalse(validate_registry(_registry(agent)).ok)


class GovernanceGateTests(unittest.TestCase):
    def test_least_privilege_rejects_default_scope(self):
        agent = _valid_agent()
        agent["least_privilege"]["graph_scopes"] = [".default"]
        result = validate_registry(_registry(agent))
        self.assertTrue(any("least privilege" in e for e in result.errors))

    def test_least_privilege_rejects_wildcard(self):
        agent = _valid_agent()
        agent["least_privilege"]["graph_scopes"] = ["*"]
        self.assertFalse(validate_registry(_registry(agent)).ok)

    def test_personal_data_requires_human_oversight(self):
        agent = _valid_agent()
        agent["data"]["personal_data"] = True
        agent["human_oversight"] = ""
        result = validate_registry(_registry(agent))
        self.assertFalse(result.ok)
        self.assertTrue(any("human_oversight" in e for e in result.errors))

    def test_employment_decision_requires_codetermination_obtained(self):
        agent = _valid_agent()
        agent["data"]["employment_decision"] = True
        agent["co_determination"] = "not_required"
        result = validate_registry(_registry(agent))
        self.assertTrue(any("BetrVG" in e for e in result.errors))

    def test_employment_decision_passes_when_codetermination_obtained(self):
        agent = _valid_agent()
        agent["data"]["employment_decision"] = True
        agent["co_determination"] = "obtained"
        self.assertTrue(validate_registry(_registry(agent)).ok)


class Phase2AccessTests(unittest.TestCase):
    def _registry_with_defaults(self, catalogue, *agents):
        return {
            "version": 1,
            "defaults": {"azure_role_catalogue": list(catalogue)},
            "agents": list(agents),
        }

    def test_azure_role_outside_catalogue_fails(self):
        agent = _valid_agent()
        agent["least_privilege"]["azure_roles"] = ["Owner"]
        result = validate_registry(
            self._registry_with_defaults(["Azure AI User"], agent)
        )
        self.assertTrue(any("azure_role_catalogue" in e for e in result.errors))

    def test_azure_role_within_catalogue_passes(self):
        agent = _valid_agent()
        agent["least_privilege"]["azure_roles"] = ["Azure AI User"]
        self.assertTrue(
            validate_registry(
                self._registry_with_defaults(["Azure AI User"], agent)
            ).ok
        )

    def test_valid_access_block_passes(self):
        agent = _valid_agent()
        agent["access"] = {
            "conditional_access": "ca-agents-baseline",
            "sensitive_data_groups": {"sample-data": ["grp-x"]},
        }
        self.assertTrue(validate_registry(_registry(agent)).ok)

    def test_gated_scope_not_in_data_scopes_fails(self):
        agent = _valid_agent()
        agent["access"] = {
            "conditional_access": "ca-agents-baseline",
            "sensitive_data_groups": {"unknown-scope": ["grp-x"]},
        }
        result = validate_registry(_registry(agent))
        self.assertTrue(any("not in" in e and "data_scopes" in e for e in result.errors))

    def test_empty_group_list_fails(self):
        agent = _valid_agent()
        agent["access"] = {
            "conditional_access": "ca-agents-baseline",
            "sensitive_data_groups": {"sample-data": []},
        }
        self.assertFalse(validate_registry(_registry(agent)).ok)

    def test_special_category_requires_group_gate(self):
        agent = _valid_agent()
        agent["data"]["special_category"] = True
        # no access block at all
        result = validate_registry(_registry(agent))
        self.assertTrue(any("special-category" in e for e in result.errors))

    def test_special_category_with_gate_passes(self):
        agent = _valid_agent()
        agent["data"]["special_category"] = True
        agent["access"] = {
            "conditional_access": "ca-agents-baseline",
            "sensitive_data_groups": {"sample-data": ["grp-x"]},
        }
        self.assertTrue(validate_registry(_registry(agent)).ok)


if __name__ == "__main__":
    unittest.main()
