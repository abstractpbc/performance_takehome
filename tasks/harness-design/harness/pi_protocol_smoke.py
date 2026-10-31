"""Explicit CPU-only stock-Pi read/edit/complete-proposal preflight.

Default inactive. --execute-fixture uses deterministic loopback HTTP responses;
--execute-live requires an independently approved, budgeted loopback model gateway.
Neither mode allocates a GPU, builds Docker images or changes the source context.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from abstract_harness.pi_preflight import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
