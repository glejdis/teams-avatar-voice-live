"""Bridge to the Phase 2 entitlement checker, kept import-safe.

``AgentGuard`` (security) needs the Entra-group entitlement gate that lives in
``agentgov.auth.entitlements`` (identity). To avoid a hard dependency on the
auth package's import chain, this module imports it lazily and degrades to an
allow-all checker (DLP still applies) if it isn't available.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class _AllowAllChecker:
    """Fallback when the entitlement library is unavailable — never gates."""

    def is_allowed(self, data_scope: str, user_group_ids: Iterable[str]) -> bool:  # noqa: ARG002
        return True


def build_entitlement_checker(registry: dict) -> Any:
    try:
        from agentgov.auth.entitlements import (
            EntitlementChecker,
            load_policy_from_registry,
        )

        return EntitlementChecker(load_policy_from_registry(registry))
    except Exception:  # noqa: BLE001
        logger.warning(
            "agentgov.auth.entitlements unavailable; sensitive-data entitlement "
            "gating disabled (DLP still applies).",
            exc_info=True,
        )
        return _AllowAllChecker()
