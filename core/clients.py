"""AI client factories — shared building block for downstream consumers.

This module is *not* used by ``hosted-agent/main.py`` (which builds its own
``AzureOpenAIChatClient`` against the Foundry endpoint at startup) or by
``launcher/`` (which only talks to Microsoft Graph, not to a model). It is
kept here so external consumers — or future hosted-agent variants that
need a Foundry chat client outside the agent-server runtime — can reuse
the credential-pinning and singleton-cache logic instead of re-implementing
it.

Two client flavours are supported:

- ``FoundryChatClient`` — the default for Agent Framework agents pointed at
  a Foundry project. Uses ``DefaultAzureCredential`` (or ``AzureCliCredential``
  with an explicit tenant when ``AZURE_TENANT_ID`` is set).
- ``OpenAIChatCompletionClient`` — the OpenAI-compatible surface of Azure AI
  Foundry. Uses ``AzureCliCredential`` and a base URL derived from the
  Foundry project endpoint.

Both factories support an optional singleton cache (keyed by
``(client_type, model, endpoint)``) so repeated calls within one process
reuse the same client.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from .config import AgentConfig

logger = logging.getLogger(__name__)

# Cache: {(client_type, model, endpoint): client_instance}
_client_cache: dict[tuple[str, str, str], Any] = {}


def _get_tenant_id() -> Optional[str]:
    """Resolve the Azure tenant to pin credentials to.

    Avoids the classic "Tenant provided in token does not match resource token"
    error that happens when DefaultAzureCredential picks up a token from the
    wrong identity (e.g. VS / shared token cache on a different tenant than
    the Foundry resource).

    Resolution order:
      1. AZURE_TENANT_ID env var
      2. FOUNDRY_TENANT_ID env var
      3. None — let DefaultAzureCredential figure it out
    """
    return (
        os.getenv("AZURE_TENANT_ID")
        or os.getenv("FOUNDRY_TENANT_ID")
        or None
    )


def _base_url_from_endpoint(endpoint: str) -> str:
    """Extract `https://<resource>.services.ai.azure.com` from a Foundry endpoint.

    Raises RuntimeError if the endpoint does not match the expected shape.
    """
    m = re.match(r"(https://[^/]+\.azure\.com)", endpoint)
    if not m:
        raise RuntimeError(
            f"Could not parse Azure base URL from FOUNDRY_PROJECT_ENDPOINT: {endpoint!r}"
        )
    return m.group(1)


def get_foundry_chat_client(
    config: Optional[AgentConfig] = None,
    *,
    singleton: bool = True,
) -> Any:
    """Create or return a shared `FoundryChatClient`.

    Args:
        config: AgentConfig. If None, loads from environment.
        singleton: If True (default), reuse a cached instance keyed by
            (model, endpoint). Set False to force a new instance.

    Returns:
        A `FoundryChatClient` instance ready to be passed to `Agent(...)`.
    """
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import AzureCliCredential, DefaultAzureCredential

    cfg = config or AgentConfig.from_env()
    endpoint = cfg.require_endpoint()
    key = ("foundry", cfg.model, endpoint)

    if singleton and key in _client_cache:
        return _client_cache[key]

    tenant_id = _get_tenant_id()
    if tenant_id:
        # DefaultAzureCredential's tenant_id kwargs do NOT apply to its
        # AzureCliCredential leg — the az CLI just uses whatever tenant
        # `az account` defaults to. On dev boxes logged into multiple
        # tenants this silently returns a token from the WRONG tenant and
        # the Foundry API responds 400 "Tenant provided in token does not
        # match resource token". Use AzureCliCredential directly with an
        # explicit tenant_id so az is called with `--tenant <id>`.
        credential: Any = AzureCliCredential(tenant_id=tenant_id)
    else:
        credential = DefaultAzureCredential()

    client = FoundryChatClient(
        project_endpoint=endpoint,
        model=cfg.model,
        credential=credential,
    )
    logger.info(
        "FoundryChatClient created (app=%s, model=%s, endpoint=%s…, tenant=%s)",
        cfg.app_name,
        cfg.model,
        endpoint[:50],
        tenant_id or "(auto)",
    )

    if singleton:
        _client_cache[key] = client
    return client


def get_openai_chat_completion_client(
    config: Optional[AgentConfig] = None,
    *,
    singleton: bool = True,
) -> Any:
    """Create or return a shared `OpenAIChatCompletionClient` (Agent Framework).

    This is the client flavor used by cv-screening-agent-v2 — it goes through
    the OpenAI-compatible surface of Azure AI Foundry using `AzureCliCredential`.

    Args:
        config: AgentConfig. If None, loads from environment.
        singleton: If True, reuse a cached instance.

    Returns:
        An `OpenAIChatCompletionClient` instance.
    """
    from agent_framework.openai import OpenAIChatCompletionClient
    from azure.identity import AzureCliCredential

    cfg = config or AgentConfig.from_env()
    endpoint = cfg.require_endpoint()
    base_url = _base_url_from_endpoint(endpoint)
    key = ("openai", cfg.model, base_url)

    if singleton and key in _client_cache:
        return _client_cache[key]

    tenant_id = _get_tenant_id()
    cli_kwargs: dict[str, Any] = {}
    if tenant_id:
        cli_kwargs["tenant_id"] = tenant_id

    client = OpenAIChatCompletionClient(
        model=cfg.model,
        azure_endpoint=base_url,
        api_version=cfg.api_version,
        credential=AzureCliCredential(**cli_kwargs),
    )
    logger.info(
        "OpenAIChatCompletionClient created (app=%s, model=%s, base_url=%s)",
        cfg.app_name,
        cfg.model,
        base_url,
    )

    if singleton:
        _client_cache[key] = client
    return client


def clear_client_cache() -> None:
    """Clear the singleton cache — useful for tests."""
    _client_cache.clear()
