"""launcher — Teams meeting + email invite + avatar bot dispatch.

This package is the **entry point** for the ``teams_avatar_voice_live`` repo
when used as a CLI or thin service. It glues three concerns together:

1. :mod:`launcher.graph_client` — Microsoft Graph helpers to create a Teams
   online meeting and send the invite email via ``Mail.Send`` (delegated
   fallback supported).
2. :mod:`launcher.bot_dispatcher` — single ``dispatch(join_url, mode=...)``
   call that hands the meeting URL off to either the **VMSS Graph bot**
   (``graph_bot``) or the **ACS browser WebRTC** fallback
   (``browser_webrtc``).
3. :mod:`launcher.cli` — argparse front-door so an operator can run
   ``python -m launcher schedule --to alice@example.com --start "+5"``
   from a shell.

Typical use::

    python -m launcher schedule \\
        --to alice@example.com \\
        --start +10 \\
        --duration-mins 30 \\
        --subject "Demo interview" \\
        --mode graph_bot

See ``README.md`` (root) for the full end-to-end quickstart.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
