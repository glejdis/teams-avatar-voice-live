"""Unit tests for agentgov.auth.entitlements (Phase 2 access governance).

Run with::

    python -m unittest discover -s agentgov/tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentgov.auth import (  # noqa: E402
    EntitlementChecker,
    EntitlementError,
    SensitiveScopePolicy,
    extract_group_ids,
    load_policy_from_registry,
)

_REGISTRY = _REPO_ROOT / "governance" / "agent-registry.yaml"


def _checker() -> EntitlementChecker:
    policy = SensitiveScopePolicy(
        scope_groups={
            "employee-records": frozenset({"grp-hr-sensitive"}),
            "candidate-pipeline": frozenset({"grp-recruiters", "grp-ta-leads"}),
        }
    )
    return EntitlementChecker(policy)


class EntitlementCheckerTests(unittest.TestCase):
    def test_ungated_scope_is_always_allowed(self):
        c = _checker()
        self.assertTrue(c.is_allowed("training-status", []))

    def test_member_of_required_group_is_allowed(self):
        c = _checker()
        self.assertTrue(c.is_allowed("employee-records", ["grp-hr-sensitive", "grp-all"]))

    def test_any_of_groups_grants_access(self):
        c = _checker()
        self.assertTrue(c.is_allowed("candidate-pipeline", ["grp-ta-leads"]))

    def test_case_insensitive_group_match(self):
        c = _checker()
        self.assertTrue(c.is_allowed("employee-records", ["GRP-HR-Sensitive"]))

    def test_exit_criterion_removing_group_denies_access(self):
        c = _checker()
        # User was in the group -> allowed.
        self.assertTrue(c.is_allowed("employee-records", ["grp-hr-sensitive"]))
        # Group membership removed -> access immediately denied.
        self.assertFalse(c.is_allowed("employee-records", ["grp-all"]))

    def test_assert_allowed_raises_for_denied(self):
        c = _checker()
        with self.assertRaises(EntitlementError) as ctx:
            c.assert_allowed("employee-records", ["grp-all"])
        self.assertEqual(ctx.exception.data_scope, "employee-records")
        self.assertIn("grp-hr-sensitive", ctx.exception.required_groups)

    def test_filter_allowed_scopes(self):
        c = _checker()
        allowed = c.filter_allowed_scopes(
            ["training-status", "employee-records", "candidate-pipeline"],
            ["grp-hr-sensitive"],
        )
        self.assertIn("training-status", allowed)       # ungated
        self.assertIn("employee-records", allowed)       # in group
        self.assertNotIn("candidate-pipeline", allowed)  # not in group


class ExtractGroupIdsTests(unittest.TestCase):
    def test_graph_member_of_payload(self):
        payload = {"value": [{"id": "g1"}, {"id": "g2"}, {"displayName": "no-id"}]}
        self.assertEqual(extract_group_ids(payload), ["g1", "g2"])

    def test_list_of_ids(self):
        self.assertEqual(extract_group_ids(["a", "b"]), ["a", "b"])

    def test_unknown_shape(self):
        self.assertEqual(extract_group_ids(None), [])


class LoadPolicyFromRegistryTests(unittest.TestCase):
    def test_builds_policy_from_dict(self):
        registry = {
            "agents": [
                {
                    "id": "x",
                    "access": {
                        "sensitive_data_groups": {
                            "employee-records": ["grp-hr-sensitive"]
                        }
                    },
                },
                {
                    "id": "y",
                    "access": {
                        "sensitive_data_groups": {
                            "employee-records": ["grp-hr-leads"],
                            "candidate-pipeline": ["grp-recruiters"],
                        }
                    },
                },
                {"id": "z"},  # no access block -> ignored
            ]
        }
        policy = load_policy_from_registry(registry)
        # union across agents (any-of)
        self.assertEqual(
            policy.required_groups("employee-records"),
            frozenset({"grp-hr-sensitive", "grp-hr-leads"}),
        )
        self.assertTrue(policy.is_sensitive("candidate-pipeline"))
        self.assertFalse(policy.is_sensitive("training-status"))

    def test_shipped_registry_loads_and_gates_sensitive_scopes(self):
        if not _REGISTRY.exists():
            self.skipTest("registry not present")
        from agentgov.auth.entitlements import load_policy_from_registry_file

        policy = load_policy_from_registry_file(_REGISTRY)
        # The shipped registry gates at least one sensitive scope.
        self.assertTrue(policy.sensitive_scopes)


if __name__ == "__main__":
    unittest.main()
