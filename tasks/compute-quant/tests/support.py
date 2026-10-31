"""Shared helpers for the Compute Quant test suite (candidate-owned)."""

from __future__ import annotations

import copy
import csv
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TASK_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TASK_DIR.parents[1]
DATA_DIR = TASK_DIR / "data"
DEMO_CASE = DATA_DIR / "demo-case.json"
RUN_PY = TASK_DIR / "research" / "run.py"
STANDINS = TASK_DIR / "standins" / "perturbations"

if str(TASK_DIR) not in sys.path:
    sys.path.insert(0, str(TASK_DIR))

from research.pipeline import load_config, run_pipeline  # noqa: E402


def demo_quote_rows() -> list[dict[str, str]]:
    with (DATA_DIR / "demo-quotes.csv").open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def demo_case_mapping() -> dict:
    return json.loads(DEMO_CASE.read_text(encoding="utf-8"))


class CaseBuilder:
    """Write a perturbed copy of the demo input pack into a temporary directory."""

    def __init__(self, test: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp(prefix="cq-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.case = demo_case_mapping()
        self.quotes = demo_quote_rows()
        self.columns = list(self.quotes[0].keys())
        shutil.copy(DATA_DIR / "demo-performance.csv", self.dir / "demo-performance.csv")

    def quote(self, qid: str) -> dict[str, str]:
        return next(row for row in self.quotes if row["id"] == qid)

    def drop(self, *ids: str) -> CaseBuilder:
        self.quotes = [row for row in self.quotes if row["id"] not in ids]
        return self

    def set(self, qid: str, **fields: str) -> CaseBuilder:
        for key in fields:
            if key not in self.columns:
                self.columns.append(key)
        self.quote(qid).update({k: str(v) for k, v in fields.items()})
        return self

    def add(self, **fields: str) -> CaseBuilder:
        row = {column: "" for column in self.columns}
        for key, value in fields.items():
            if key not in self.columns:
                self.columns.append(key)
                for existing in self.quotes:
                    existing.setdefault(key, "")
            row[key] = str(value)
        self.quotes.append(row)
        return self

    def copy_quote(self, qid: str, new_id: str, **fields: str) -> CaseBuilder:
        row = copy.deepcopy(self.quote(qid))
        row["id"] = new_id
        for key in fields:
            if key not in self.columns:
                self.columns.append(key)
                for existing in self.quotes:
                    existing.setdefault(key, "")
        row.update({k: str(v) for k, v in fields.items()})
        self.quotes.append(row)
        return self

    def write(self) -> Path:
        with (self.dir / "demo-quotes.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.columns, lineterminator="\n")
            writer.writeheader()
            for row in self.quotes:
                writer.writerow({column: row.get(column, "") for column in self.columns})
        path = self.dir / "demo-case.json"
        path.write_text(json.dumps(self.case, indent=2) + "\n", encoding="utf-8")
        return path

    def run(self, config: dict | None = None, **kwargs) -> dict:
        return run_pipeline(self.write(), config or load_config(None), **kwargs)


def run_demo(**kwargs) -> dict:
    return run_pipeline(DEMO_CASE, load_config(None), **kwargs)


def node(results: dict, tenor: int) -> dict:
    return next(n for n in results["curve"]["nodes"] if n["tenor_months"] == tenor)


def quote_row(results: dict, qid: str) -> dict:
    return next(q for q in results["_quote_table"] if q["id"] == qid)


def width(node_dict: dict) -> float:
    return node_dict["usd_per_gpu_hour_high"] - node_dict["usd_per_gpu_hour_low"]


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(RUN_PY), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
