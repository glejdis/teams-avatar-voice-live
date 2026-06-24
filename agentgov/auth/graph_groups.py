"""Resolve a signed-in user's Entra group memberships via Microsoft Graph.

Best-effort, **non-raising** helper that feeds the entitlement gate
(``access.sensitive_data_groups``) with the caller's *real* group object ids
instead of a hard-coded bridge. Given a delegated Microsoft Graph access token —
the avatar's ``launcher`` already acquires one via MSAL — it calls
``/me/memberOf`` and returns the group ids; it returns ``[]`` on any failure
(missing ``GroupMember.Read.All`` consent, network, bad token) so callers can
fall back to a configured group rather than break.

Reuses :func:`agentgov.auth.entitlements.extract_group_ids` to parse the Graph
payload. ``requests`` is imported lazily so importing this module never pulls a
hard dependency; pass your own ``session`` (e.g. in tests) to avoid it entirely.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .entitlements import extract_group_ids

logger = logging.getLogger(__name__)

_GRAPH_MEMBER_OF = "https://graph.microsoft.com/v1.0/me/memberOf?$select=id&$top=200"
_DEFAULT_TIMEOUT = 10.0


def resolve_group_ids(
    access_token: str,
    *,
    session: Optional[Any] = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[str]:
    """Return the caller's Entra group object ids, or ``[]`` on any failure.

    Args:
        access_token: a delegated Microsoft Graph access token for the signed-in
            user (needs a directory-read scope such as ``GroupMember.Read.All``).
        session: an optional ``requests``-style session (``.get`` returning an
            object with ``.ok``/``.status_code``/``.json()``); created lazily if
            omitted.
        timeout: per-request timeout in seconds.
    """
    if not access_token or not str(access_token).strip():
        return []
    try:
        if session is None:
            import requests  # lazy — keep the import optional

            session = requests.Session()
        response = session.get(
            _GRAPH_MEMBER_OF,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
        if not getattr(response, "ok", False):
            logger.info(
                "Graph /me/memberOf returned %s; continuing without groups.",
                getattr(response, "status_code", "?"),
            )
            return []
        return extract_group_ids(response.json() or {})
    except Exception:  # noqa: BLE001
        logger.info("Group-membership resolution failed; continuing without groups.", exc_info=True)
        return []
