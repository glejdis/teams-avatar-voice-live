"""Identity-layer governance: sensitive-data entitlement enforcement."""

from __future__ import annotations

from .entitlements import (
    EntitlementChecker,
    EntitlementError,
    SensitiveScopePolicy,
    extract_group_ids,
    load_policy_from_registry,
    load_policy_from_registry_file,
)
from .graph_groups import resolve_group_ids

__all__ = [
    "EntitlementChecker",
    "EntitlementError",
    "SensitiveScopePolicy",
    "extract_group_ids",
    "load_policy_from_registry",
    "load_policy_from_registry_file",
    "resolve_group_ids",
]
