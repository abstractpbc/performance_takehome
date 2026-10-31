"""Make the harness package and the repo root importable from the test tree."""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
HARNESS_DIR = TESTS_DIR.parent
TASK_DIR = HARNESS_DIR.parent
REPO_ROOT = TASK_DIR.parents[1]
for p in (REPO_ROOT, HARNESS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
