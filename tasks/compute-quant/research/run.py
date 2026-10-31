"""Single CLI entrypoint for the Compute Quant pipeline.

Usage (from the repository root, no network, stdlib only)::

    python tasks/compute-quant/research/run.py \
        --case tasks/compute-quant/data/demo-case.json \
        --out tasks/compute-quant/outputs/

Writes ``curve.csv``, ``valuation.json`` and ``run-manifest.json`` into ``--out``. Add
``--report PATH`` to render ``REPORT.md`` from the same numbers.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    if sys.version_info < (3, 11):  # noqa: UP036 - clear message on older interpreters
        sys.stderr.write(f"error: Python 3.11+ is required, found {sys.version.split()[0]}\n")
        raise SystemExit(2)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from research.cli import main

    raise SystemExit(main())
