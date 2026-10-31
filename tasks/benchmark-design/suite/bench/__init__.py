"""CANDIDATE-BUILT benchmark suite library for Task 1 (benchmark-design).

Importing this package makes the repository root importable so that the shared,
organizer-owned primitives (``benchmarks.metrics``, ``benchmarks.streaming``) can be
reused without modification whether the suite is run as a script, a module, or a test.
"""

import sys
from pathlib import Path

SUITE_DIR = Path(__file__).resolve().parent.parent
TASK_DIR = SUITE_DIR.parent
REPO_ROOT = TASK_DIR.parent.parent

for _path in (REPO_ROOT, SUITE_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

SUITE_VERSION = "1.0.0"
