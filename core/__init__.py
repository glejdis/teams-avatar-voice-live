"""core — shared building blocks for teams-avatar-voice-live.

Tiny, opinion-free helpers shared by ``launcher/`` and ``hosted-agent/``:

- :mod:`core.clients` — Foundry / OpenAI client factories.
- :mod:`core.config` — :class:`AgentConfig` (model, temperature, timeouts)
  loaded from environment variables.
- :mod:`core._bootstrap` — optional ``sys.path`` shim so child apps can
  ``import core`` without ``pip install -e``.

Typical use:

.. code-block:: python

    from core.clients import get_foundry_chat_client
    from core.config import AgentConfig

    cfg = AgentConfig.from_env()
    client = get_foundry_chat_client(cfg)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "__version__",
]
