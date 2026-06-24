import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apply_tenant_config import (  # noqa: E402
    find_unmapped,
    is_placeholder,
    resolve_registry,
)
from validate_registry import DEFAULT_REGISTRY, load_registry  # noqa: E402

_ZERO = "00000000-0000-0000-0000-000000000000"
_REAL = "11111111-2222-3333-4444-555555555555"


def _registry():
    return load_registry(DEFAULT_REGISTRY)


def _full_config():
    """A config that maps every placeholder the shipped registry uses."""
    return {
        "tenant_id": _REAL,
        "groups": {"grp-tva-recruiters": _REAL},
        "conditional_access": {"ca-agents-baseline": _REAL},
        "identities": {"id-tva-lisa": _REAL},
    }


class IsPlaceholderTests(unittest.TestCase):
    def test_zero_guid_is_placeholder(self):
        self.assertTrue(is_placeholder(_ZERO))

    def test_empty_and_none_are_placeholders(self):
        self.assertTrue(is_placeholder(""))
        self.assertTrue(is_placeholder(None))
        self.assertTrue(is_placeholder("<set-me>"))

    def test_real_guid_is_not_placeholder(self):
        self.assertFalse(is_placeholder(_REAL))


class FindUnmappedTests(unittest.TestCase):
    def test_example_config_is_fully_unmapped(self):
        # Every value is the all-zero placeholder -> all flagged.
        cfg = {
            "groups": {"grp-tva-recruiters": _ZERO},
            "conditional_access": {"ca-agents-baseline": _ZERO},
            "identities": {"id-tva-lisa": _ZERO},
        }
        problems = find_unmapped(cfg, _registry())
        joined = "\n".join(problems)
        self.assertIn("groups['grp-tva-recruiters']", joined)
        self.assertIn("conditional_access['ca-agents-baseline']", joined)
        self.assertIn("identities['id-tva-lisa']", joined)

    def test_full_config_has_no_problems(self):
        self.assertEqual(find_unmapped(_full_config(), _registry()), [])

    def test_missing_group_is_flagged(self):
        cfg = _full_config()
        cfg["groups"] = {}
        problems = find_unmapped(cfg, _registry())
        self.assertTrue(any("grp-tva-recruiters" in p for p in problems))


class ResolveRegistryTests(unittest.TestCase):
    def test_group_names_replaced_with_ids(self):
        resolved = resolve_registry(_registry(), _full_config())
        agent = resolved["agents"][0]
        groups = agent["access"]["sensitive_data_groups"]["interview-transcript"]
        self.assertEqual(groups, [_REAL])

    def test_exit_criterion_unmapped_blocks_resolution(self):
        # The shipped example (all-zero) must NOT pass the gate.
        cfg = {
            "groups": {"grp-tva-recruiters": _ZERO},
            "conditional_access": {"ca-agents-baseline": _ZERO},
            "identities": {"id-tva-lisa": _ZERO},
        }
        self.assertNotEqual(find_unmapped(cfg, _registry()), [])


if __name__ == "__main__":
    unittest.main()
