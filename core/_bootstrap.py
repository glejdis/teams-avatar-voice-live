"""Bootstrap helper — adds the repo root to sys.path so `core`
can be imported from any sub-app without `pip install`.

Usage at the top of an app entry point (app.py / run.py):

    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # now `import core` works

Or, equivalently:

    import core._bootstrap  # noqa: F401  (idempotent)
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
