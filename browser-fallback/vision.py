"""Vision wrapper for Lisa's screen-share commentary.

A thin, dependency-light layer that takes a JPEG byte string and returns a
short natural-language description, using a multimodal Azure OpenAI deployment.

By default we reuse the same Foundry / Azure OpenAI resource Lisa already runs
against (``AZURE_VOICELIVE_ENDPOINT`` ends up pointing at the same account).
``gpt-4.1-mini`` is multimodal, so no extra deployment is required.

The module exposes:

* ``is_configured()``  — cheap synchronous check used by ``/api/config``.
* ``describe_frame(jpeg_bytes, *, trigger)`` — async call returning the
  ≤2-sentence description.

Auth precedence:
1. ``AZURE_OPENAI_VISION_API_KEY`` if set (simplest for local dev).
2. ``DefaultAzureCredential`` AAD bearer token (matches the rest of the
   ``browser-fallback`` package).

The prompt is deliberately strict about privacy: no verbatim URLs, emails,
phone numbers, or long numeric IDs. Lisa is expected to summarize.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

from azure.identity.aio import (
    AzureCliCredential,
    DefaultAzureCredential,
    get_bearer_token_provider,
)
from openai import AsyncAzureOpenAI

logger = logging.getLogger("lisa-demo.vision")

_DEFAULT_DEPLOYMENT = "gpt-4.1-mini"
_DEFAULT_API_VERSION = "2024-12-01-preview"
_DEFAULT_SYSTEM_PROMPT = (
    "You are Lisa, a Microsoft cloud solution architect looking at a single "
    "screenshot of the other participant's shared screen during a live spoken "
    "review call. Do three things, in order: (1) Identify the artifact "
    "specifically and concretely - say whether it is an architecture diagram, "
    "infrastructure-as-code, an Azure portal blade, a dashboard, code, or a "
    "document, name the key components or services you can see, and note the "
    "main data flows or structure. (2) Briefly assess it against the Azure "
    "Well-Architected pillars (reliability, security, cost optimization, "
    "operational excellence, performance efficiency), focusing only on what is "
    "actually visible. (3) Offer one or two concrete, prioritized "
    "recommendations or a sensible next step. Speak naturally, as if talking, "
    "in about three to five short sentences. Be candid but collaborative, and "
    "defer to the human engineer on final decisions. Never read URLs, email "
    "addresses, phone numbers, or long numeric identifiers verbatim - "
    "summarize them. If the screen is blank, black, heavily blurred, or "
    "unreadable, say that plainly and do NOT guess or invent any content."
)

_client: Optional[AsyncAzureOpenAI] = None
_credential: Optional[DefaultAzureCredential] = None


def _derived_endpoint() -> str:
    """Resolve the Azure OpenAI endpoint, deriving from Voice Live if needed."""
    explicit = os.getenv("AZURE_OPENAI_VISION_ENDPOINT", "").strip()
    if explicit:
        return explicit.rstrip("/")
    voicelive = os.getenv("AZURE_VOICELIVE_ENDPOINT", "").strip().rstrip("/")
    if not voicelive:
        return ""
    # Voice Live exposes either …cognitiveservices.azure.com or
    # …services.ai.azure.com — both back onto the same AOAI account, which is
    # reachable as …openai.azure.com.
    for suffix in (".cognitiveservices.azure.com", ".services.ai.azure.com"):
        if suffix in voicelive:
            return voicelive.replace(suffix, ".openai.azure.com")
    return voicelive


def is_configured() -> bool:
    """Return True when a vision endpoint can be resolved.

    The actual AOAI call may still fail (deployment missing, auth, quota); this
    is just the "should we expose the UI affordance?" check used by /api/config.
    """
    return bool(_derived_endpoint())


async def _get_client() -> AsyncAzureOpenAI:
    global _client, _credential
    if _client is not None:
        return _client

    endpoint = _derived_endpoint()
    if not endpoint:
        raise RuntimeError(
            "Set AZURE_OPENAI_VISION_ENDPOINT or AZURE_VOICELIVE_ENDPOINT to enable vision."
        )

    api_version = os.getenv("AZURE_OPENAI_VISION_API_VERSION", _DEFAULT_API_VERSION)
    api_key = os.getenv("AZURE_OPENAI_VISION_API_KEY", "").strip()

    if api_key:
        logger.info("Vision client using API key auth (endpoint=%s)", endpoint)
        _client = AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        return _client

    logger.info("Vision client using DefaultAzureCredential (endpoint=%s)", endpoint)
    # Pin the tenant on multi-tenant dev boxes: DefaultAzureCredential's
    # AzureCliCredential leg ignores tenant kwargs and uses az's default tenant,
    # which can be the wrong one. Mirror app.py's _build_credential so the vision
    # token comes from the same tenant as the rest of the package.
    tenant_id = os.getenv("AZURE_TENANT_ID") or os.getenv("FOUNDRY_TENANT_ID")
    if tenant_id:
        logger.info("Vision client pinning tenant via AzureCliCredential (tenant=%s)", tenant_id)
        _credential = AzureCliCredential(tenant_id=tenant_id)
    else:
        _credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        _credential,
        "https://cognitiveservices.azure.com/.default",
    )
    _client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=api_version,
    )
    return _client


async def describe_frame(jpeg_bytes: bytes, *, trigger: str = "on_demand") -> tuple[str, Optional[dict]]:
    """Return a short spoken architecture-review comment plus token usage.

    Returns ``(description, usage)`` where ``usage`` is ``{"prompt_tokens",
    "completion_tokens", "total_tokens"}`` (or ``None`` if the service did not
    report it). Raises a runtime error if the vision call fails so the caller
    can log and surface the failure without spoiling the Voice Live session.
    """
    if not jpeg_bytes:
        raise ValueError("describe_frame called with empty bytes")

    deployment = os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT", _DEFAULT_DEPLOYMENT)
    client = await _get_client()
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")

    system_prompt = os.getenv("AZURE_OPENAI_VISION_SYSTEM_PROMPT", "").strip() or _DEFAULT_SYSTEM_PROMPT

    user_text = (
        "Look at the shared screen. First say specifically what it is and the "
        "key components you can see, then briefly assess it against the Azure "
        "Well-Architected pillars, then give one or two prioritized "
        f"recommendations. Trigger: {trigger}. Keep it to about three to five "
        "short sentences."
    )

    try:
        response = await client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "high"},
                        },
                    ],
                },
            ],
            max_completion_tokens=320,
            temperature=0.3,
        )
    except Exception as exc:
        logger.exception("Vision describe_frame failed")
        raise RuntimeError(f"vision call failed: {exc}") from exc

    description = (response.choices[0].message.content or "").strip()
    if not description:
        raise RuntimeError("vision call returned an empty description")

    usage_obj = getattr(response, "usage", None)
    usage: Optional[dict] = None
    if usage_obj is not None:
        usage = {
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
        }
    return description, usage


async def aclose() -> None:
    """Best-effort cleanup of the singleton client + credential (test/teardown)."""
    global _client, _credential
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            logger.debug("vision client close raised", exc_info=True)
        _client = None
    if _credential is not None:
        try:
            await _credential.close()
        except Exception:
            logger.debug("vision credential close raised", exc_info=True)
        _credential = None


__all__ = ["is_configured", "describe_frame", "aclose"]
