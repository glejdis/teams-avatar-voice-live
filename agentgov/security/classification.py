"""Map registry data sensitivity to a Microsoft Purview sensitivity label.

Thin helper so agents and the audit trail tag content with the same label the
Purview policy applies. Backed by ``sensitivity_labels`` in the DLP policy.
"""

from __future__ import annotations

from .policy import DlpPolicy


def label_for_sensitivity(policy: DlpPolicy, sensitivity: str | None) -> str:
    """Return the Purview sensitivity label for a registry sensitivity value."""
    return policy.label_for(sensitivity)
