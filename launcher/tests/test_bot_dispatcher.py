"""Tests for the avatar-transport dispatcher join-path hardening.

Covers the "no avatar joined" failure signature: a loud warning + an attributable
AGENT_AUDIT event whenever no avatar will actually enter the meeting.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from launcher import bot_dispatcher  # noqa: E402

_AUDIT_LOGGER = "agentgov.security.audit"
_DISPATCH_LOGGER = "launcher.bot_dispatcher"
_JOIN_URL = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc/0"


class GraphBotDispatchTests(unittest.TestCase):
    def setUp(self):
        self._orig = bot_dispatcher.graph_client.invite_bot_to_meeting

    def tearDown(self):
        bot_dispatcher.graph_client.invite_bot_to_meeting = self._orig

    def test_successful_join_reports_avatar_will_join(self):
        bot_dispatcher.graph_client.invite_bot_to_meeting = lambda *a, **k: {"ok": True, "status": 200}
        with self.assertLogs(_AUDIT_LOGGER, level="INFO") as cm:
            res = bot_dispatcher.dispatch(_JOIN_URL, mode="graph_bot", session_id="S1")
        self.assertEqual(res["status"], "join_requested")
        self.assertTrue(res["avatar_will_join"])
        self.assertIn("avatar.join.requested", "\n".join(cm.output))

    def test_skipped_join_warns_and_audits_failed(self):
        # e.g. BOT_JOIN_ENDPOINT not set -> invite returns skipped/ok False.
        bot_dispatcher.graph_client.invite_bot_to_meeting = lambda *a, **k: {
            "ok": False, "skipped": True, "error": "BOT_JOIN_ENDPOINT not set",
        }
        with self.assertLogs(_DISPATCH_LOGGER, level="WARNING") as dcm, \
                self.assertLogs(_AUDIT_LOGGER, level="INFO") as acm:
            res = bot_dispatcher.dispatch(_JOIN_URL, mode="graph_bot", email_sent_to="c@x.example")
        self.assertEqual(res["status"], "failed")
        self.assertFalse(res["avatar_will_join"])
        self.assertIn("AVATAR WILL NOT JOIN", "\n".join(dcm.output))
        self.assertIn("avatar.join.failed", "\n".join(acm.output))


class BrowserWebrtcDispatchTests(unittest.TestCase):
    def test_browser_mode_warns_and_audits_deferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["DEMO_LATEST_INVITE_PATH"] = str(Path(tmp) / "latest-invite.json")
            try:
                with self.assertLogs(_DISPATCH_LOGGER, level="WARNING") as dcm, \
                        self.assertLogs(_AUDIT_LOGGER, level="INFO") as acm:
                    res = bot_dispatcher.dispatch(_JOIN_URL, mode="browser_webrtc", session_id="S2")
            finally:
                os.environ.pop("DEMO_LATEST_INVITE_PATH", None)
        self.assertEqual(res["status"], "handoff_recorded")
        self.assertFalse(res["avatar_will_join"])
        self.assertIn("AVATAR JOIN DEFERRED TO BROWSER", "\n".join(dcm.output))
        self.assertIn("avatar.join.deferred", "\n".join(acm.output))


class ResolveModeTests(unittest.TestCase):
    def test_default_mode_is_graph_bot(self):
        os.environ.pop("TEAMS_JOIN_MODE", None)
        self.assertEqual(bot_dispatcher.resolve_mode(), "graph_bot")

    def test_unsupported_mode_raises(self):
        with self.assertRaises(ValueError):
            bot_dispatcher.resolve_mode("carrier_pigeon")


if __name__ == "__main__":
    unittest.main()
