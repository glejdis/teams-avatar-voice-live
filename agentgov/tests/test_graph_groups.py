"""Tests for agentgov.auth.resolve_group_ids (real Entra group membership)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentgov.auth import resolve_group_ids  # noqa: E402


class _FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        return self._response


_GROUPS = {"value": [{"id": "grp-1"}, {"id": "grp-2"}, {"displayName": "no-id"}]}


class ResolveGroupIdsTests(unittest.TestCase):
    def test_returns_group_ids_from_member_of(self):
        session = _FakeSession(_FakeResponse(200, _GROUPS))
        groups = resolve_group_ids("a-graph-token", session=session)
        self.assertEqual(groups, ["grp-1", "grp-2"])
        self.assertIn("/me/memberOf", session.calls[0]["url"])
        self.assertEqual(session.calls[0]["headers"]["Authorization"], "Bearer a-graph-token")

    def test_empty_token_returns_empty(self):
        self.assertEqual(resolve_group_ids("", session=_FakeSession(_FakeResponse(200, _GROUPS))), [])

    def test_non_ok_response_returns_empty(self):
        session = _FakeSession(_FakeResponse(403, {}))
        self.assertEqual(resolve_group_ids("tok", session=session), [])

    def test_failure_is_non_raising(self):
        class _BoomSession:
            def get(self, *a, **k):
                raise RuntimeError("network down")

        self.assertEqual(resolve_group_ids("tok", session=_BoomSession()), [])


if __name__ == "__main__":
    unittest.main()
