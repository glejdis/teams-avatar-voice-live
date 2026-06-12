"""Persona loader for the hosted agent.

Reads the persona text from ``PERSONA_FILE`` (default: ``personas/lisa.md``)
and exposes it as ``INSTRUCTIONS`` for backwards compatibility with the
original single-file persona convention.

The path in ``PERSONA_FILE`` may be absolute or relative. Relative paths
are resolved against the directory containing this file (``hosted-agent/``),
so the default value just works in both local dev and the container image.

To ship your own persona:

1. Drop a Markdown file into ``hosted-agent/personas/``.
2. Set ``PERSONA_FILE=personas/<your-file>.md`` in ``hosted-agent/.env``
   (or in the container environment).
3. Restart the agent.

See ``personas/README.md`` for authoring guidance.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_PERSONA = "personas/lisa.md"
_HERE = Path(__file__).resolve().parent


def _resolve_persona_path() -> Path:
    raw = os.environ.get("PERSONA_FILE", _DEFAULT_PERSONA).strip() or _DEFAULT_PERSONA
    path = Path(raw)
    if not path.is_absolute():
        path = _HERE / path
    return path


_PERSONA_PATH = _resolve_persona_path()

if not _PERSONA_PATH.is_file():
    raise FileNotFoundError(
        f"Persona file not found: {_PERSONA_PATH}. "
        f"Set PERSONA_FILE to a path that exists "
        f"(absolute, or relative to {_HERE})."
    )

INSTRUCTIONS: str = _PERSONA_PATH.read_text(encoding="utf-8")

__all__ = ["INSTRUCTIONS"]
