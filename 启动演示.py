"""Start the AlphaLens demo from its consolidated delivery entry."""

from __future__ import annotations

import os
import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parent


if __name__ == "__main__":
    os.chdir(ROOT)
    runpy.run_path(str(ROOT / "app" / "server.py"), run_name="__main__")
