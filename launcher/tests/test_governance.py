"""Tests for the launcher governance surface (invite-email audit)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from launcher.governance import audit_invite_email, enabled  # noqa: E402

_AUDIT_LOGGER = "agentgov.security.audit"


class AuditInviteEmailTests(unittest.TestCase):
    def test_guard_is_enabled(self):
        self.assertTrue(enabled(), "agentgov guard should load (needs PyYAML + governance/)")

    def test_emits_attributable_audit_with_recipient_email_finding(self):
        with self.assertLogs(_AUDIT_LOGGER, level="INFO") as cm:
            audit_invite_email(
                to="candidate@contoso.example",
                subject="Contoso Interview Invitation – Cashier",
                body_text="Anna Schmidt Cashier https://teams.microsoft.com/l/meetup-join/x",
                organizer_mail="recruiter@contoso.example",
            )
        line = "\n".join(cm.output)
        self.assertIn("AGENT_AUDIT", line)
        self.assertIn("invite.email", line)
        # The recipient address is a DLP email finding -> recorded in the audit.
        self.assertIn("email", line)
        self.assertIn('"direction": "output"', line)

    def test_does_not_raise_on_empty_inputs(self):
        # Should never throw — governance must not break the send path.
        audit_invite_email(to="", subject="", body_text="")


if __name__ == "__main__":
    unittest.main()
