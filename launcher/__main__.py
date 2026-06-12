"""Entry point for ``python -m launcher``.

Delegates to :func:`launcher.cli.main` so the package can be invoked as
``python -m launcher schedule ...`` from any directory after
``pip install -e .``.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
