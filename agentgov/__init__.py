"""``agentgov`` — runtime agent-governance seam for teams-avatar-voice-live.

A small, dependency-light package that lets the avatar enforce the same controls
a central Agent 365 / Microsoft Purview + Defender setup would apply, but inline
on every turn:

- :mod:`agentgov.security`  — DLP, data classification, prompt-injection
  (Defender-style) detection, attributable audit, and the one-call
  :class:`~agentgov.security.AgentGuard` seam.
- :mod:`agentgov.auth`      — sensitive-data entitlement gate (Entra-group
  membership decides who may see gated data).

Policy lives in ``governance/`` at the repo root (``agent-registry.yaml`` +
``security/dlp-policy.yaml``). The modules degrade gracefully when those files
or optional deps (PyYAML) are absent, so importing this package never breaks an
app — it just falls back to audit-only behaviour.
"""

from __future__ import annotations

__all__ = ["security", "auth"]
