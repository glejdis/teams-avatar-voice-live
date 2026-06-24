"""Lisa — Foundry hosted agent entrypoint (Responses protocol, port 8088).

Enterprise-ready single-tool screening agent:
- Uses Microsoft Agent Framework with AzureAIAgentClient (gpt-4.1-mini).
- Exposes a stateless `lookup_job_requirements` tool.
- Auth via Managed Identity (in Foundry) or DefaultAzureCredential locally.
- Wrapped by `from_agent_framework(...).run_async()` which provisions the
  OpenAI Responses protocol on http://0.0.0.0:8088/responses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Annotated

from agent_framework import Agent
from agent_framework.azure import AzureOpenAIChatClient
from azure.ai.agentserver.agentframework import from_agent_framework
from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential
from dotenv import load_dotenv
from governance_guard import build_guard_middleware
from instructions import INSTRUCTIONS
from tools.job_requirements import get_requirements_for, list_open_positions

# Load .env for local dev. In Foundry the agent runtime injects env vars,
# so missing .env is fine — load_dotenv() silently no-ops.
load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("lisa")


# ── Required env vars (injected by Foundry on the hosted agent) ──────────────
PROJECT_ENDPOINT = os.environ.get("AGENT_FOUNDRY_PROJECT_ENDPOINT") or os.environ["FOUNDRY_PROJECT_ENDPOINT"]
MODEL_DEPLOYMENT_NAME = (
    os.environ.get("AGENT_FOUNDRY_MODEL_DEPLOYMENT_NAME")
    or os.environ.get("FOUNDRY_MODEL_DEPLOYMENT_NAME")
    or "gpt-4.1-mini"
)
AZURE_OPENAI_API_VERSION = os.environ.get("AGENT_AZURE_OPENAI_API_VERSION", "2025-01-01-preview")


def _base_url_from_project_endpoint(endpoint: str) -> str:
    match = re.match(r"(https://[^/]+\.azure\.com)", endpoint)
    if not match:
        raise RuntimeError(f"Could not parse Foundry base URL from endpoint: {endpoint!r}")
    return match.group(1)


# ── Tools (stateless — traced by Foundry automatically) ──────────────────────


def lookup_job_requirements(
    position: Annotated[
        str,
        "The position the candidate is applying for, e.g. 'Cashier', "
        "'Sales Associate', 'Warehouse Clerk', 'Shift Leader', 'Part-time'. "
        "Case-insensitive, partial match supported.",
    ],
) -> dict:
    """Return the hiring criteria for one of the shipped example positions.

    Result contains must-have / nice-to-have skills, typical shifts, salary
    range, and what the hiring team looks for. For the agent's private use —
    it must NEVER read this back to the candidate verbatim.

    The shipped position catalogue is illustrative only (see
    ``tools/job_requirements.py``). Replace it with your own data source —
    or remove this tool entirely — when you ship a non-recruiting persona.
    """
    reqs = get_requirements_for(position)
    if reqs is None:
        return {
            "found": False,
            "requested": position,
            "open_positions": list_open_positions(),
            "note": (
                "No exact match. Lisa: ask the candidate which of the open "
                "positions they meant, without listing the criteria."
            ),
        }
    return {"found": True, **reqs}


# ── Credential factory ──────────────────────────────────────────────────────


def _get_credential():
    """Prefer Managed Identity in Azure; fall back to DefaultAzureCredential locally."""
    client_id = os.environ.get("AZURE_CLIENT_ID")
    if client_id:
        logger.info("Using ManagedIdentityCredential (client_id=%s)", client_id)
        return ManagedIdentityCredential(client_id=client_id)
    logger.info("Using DefaultAzureCredential")
    return DefaultAzureCredential()


# ── Agent factory ────────────────────────────────────────────────────────────


async def _build_agent() -> Agent:
    credential = _get_credential()
    base_url = _base_url_from_project_endpoint(PROJECT_ENDPOINT)
    client = AzureOpenAIChatClient(
        deployment_name=MODEL_DEPLOYMENT_NAME,
        endpoint=base_url,
        api_version=AZURE_OPENAI_API_VERSION,
        credential=credential,
    )
    # Wrap the agent with the agentgov governance seam (prompt-injection
    # screening + DLP redaction + attributable audit on every turn). Returns
    # None and runs ungoverned if agent_framework/agentgov are unavailable.
    guard_middleware = build_guard_middleware()
    middleware = [guard_middleware] if guard_middleware else []
    agent = Agent(
        client,
        name="Lisa_HR_Screener",
        instructions=INSTRUCTIONS,
        tools=[lookup_job_requirements],
        middleware=middleware,
    )
    logger.info(
        "Lisa agent built (base_url=%s, model=%s, api_version=%s, governed=%s)",
        base_url, MODEL_DEPLOYMENT_NAME, AZURE_OPENAI_API_VERSION, bool(guard_middleware),
    )
    return agent


# ── Entrypoint ───────────────────────────────────────────────────────────────


async def _main() -> None:
    agent = await _build_agent()
    logger.info("Starting Lisa on http://0.0.0.0:8088 (Responses protocol)")
    await from_agent_framework(agent).run_async()


if __name__ == "__main__":
    asyncio.run(_main())
