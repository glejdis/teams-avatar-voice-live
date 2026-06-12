"""Centralized agent configuration.

Single source of truth for model name, temperature, timeouts, retries.
Loaded from environment variables once; passed explicitly to agents.

Env var resolution order (first non-empty wins):
    FOUNDRY_PROJECT_ENDPOINT → AZURE_AI_PROJECT_ENDPOINT
    FOUNDRY_MODEL_DEPLOYMENT_NAME → FOUNDRY_MODEL → AZURE_OPENAI_DEPLOYMENT
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# load .env without overriding existing env vars (Foundry runtime vars win)
load_dotenv(override=False)


def _first_env(*keys: str, default: str = "") -> str:
    for k in keys:
        v = os.getenv(k)
        if v and v.strip():
            return v.strip()
    return default


@dataclass
class AgentConfig:
    """Configuration for AI client + agent runtime behavior.

    All fields have sensible defaults and are overridable per-app or
    per-agent. Use `AgentConfig.from_env()` to load from environment,
    or construct directly for tests.
    """

    # ── Foundry / model ──────────────────────────────────────────────
    endpoint: str = ""
    model: str = "gpt-5.3-chat"
    api_version: str = "2025-01-01-preview"

    # ── Runtime tuning ───────────────────────────────────────────────
    temperature: Optional[float] = None   # None = use model default
    max_tokens: Optional[int] = None
    timeout_sec: int = 120
    retry_count: int = 2

    # ── Metadata ─────────────────────────────────────────────────────
    app_name: str = "teams-avatar-voice-live"

    @classmethod
    def from_env(
        cls,
        *,
        default_model: str = "gpt-5.3-chat",
        app_name: str = "teams-avatar-voice-live",
    ) -> "AgentConfig":
        """Build an AgentConfig from environment variables.

        Args:
            default_model: Fallback model if no env var is set.
            app_name: App identifier for logging/telemetry.
        """
        endpoint = _first_env(
            "FOUNDRY_PROJECT_ENDPOINT",
            "AZURE_AI_PROJECT_ENDPOINT",
        )
        model = _first_env(
            "FOUNDRY_MODEL_DEPLOYMENT_NAME",
            "FOUNDRY_MODEL",
            "AZURE_OPENAI_DEPLOYMENT",
            default=default_model,
        )
        api_version = _first_env(
            "FOUNDRY_API_VERSION",
            "AZURE_OPENAI_API_VERSION",
            default="2025-01-01-preview",
        )

        timeout_sec = int(_first_env("AGENT_TIMEOUT_SEC", default="120") or "120")
        retry_count = int(_first_env("AGENT_RETRY_COUNT", default="2") or "2")

        temperature_raw = _first_env("AGENT_TEMPERATURE", default="")
        temperature = float(temperature_raw) if temperature_raw else None

        max_tokens_raw = _first_env("AGENT_MAX_TOKENS", default="")
        max_tokens = int(max_tokens_raw) if max_tokens_raw else None

        return cls(
            endpoint=endpoint,
            model=model,
            api_version=api_version,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_sec=timeout_sec,
            retry_count=retry_count,
            app_name=app_name,
        )

    def require_endpoint(self) -> str:
        """Return endpoint or raise if missing."""
        if not self.endpoint:
            raise RuntimeError(
                "FOUNDRY_PROJECT_ENDPOINT (or AZURE_AI_PROJECT_ENDPOINT) is not set. "
                "Add it to your .env file."
            )
        return self.endpoint
