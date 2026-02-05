from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path for `import backend.*` in environments where
# pytest's import mode doesn't automatically include it.
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

