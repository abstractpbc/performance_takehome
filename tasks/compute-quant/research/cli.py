"""Command-line interface for the Compute Quant pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .pipeline import build_manifest, load_config, run_pipeline, warn, write_outputs
from .schema import InputError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Offline, deterministic Compute Quant pipeline: "
        "curve, supply cost, reservation mark.",
    )
    parser.add_argument(
        "--case", required=True, type=Path, help="case JSON (e.g. data/demo-case.json)"
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="output directory for curve.csv, valuation.json, run-manifest.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="model configuration JSON (default: research/config/default.json)",
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="also render REPORT.md to this path"
    )
    parser.add_argument(
        "--exclude-quote",
        action="append",
        default=[],
        metavar="ID",
        help="drop a quote before modelling (evidence-removal experiments; repeatable)",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="override the case as-of (experiments only; recorded in outputs)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; any unexpected failure becomes a one-line exit-2 message."""
    try:
        return _main(argv)
    except (InputError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - last resort: keep the 0/2 exit contract
        print(
            f"error: unexpected failure while running the pipeline ({type(exc).__name__}: {exc}); "
            "please report this with the input pack",
            file=sys.stderr,
        )
        return 2


def _main(argv: list[str] | None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    results = run_pipeline(
        args.case,
        config,
        exclude_quote_ids=tuple(args.exclude_quote),
        as_of_override=args.as_of,
    )
    for message in results["curve"]["warnings"]:
        warn(message)
    for key in results["inputs"]["unknown_nested_case_keys"]:
        warn(f"case key {key!r} is not part of schema_version 1 and was ignored")
    for key in results["inputs"]["unknown_config_keys"]:
        warn(f"config key {key!r} is not part of config_version 1 and was ignored")
    for key in results["inputs"]["unknown_case_keys"]:
        warn(f"case key {key!r} is not part of schema_version 1 and was ignored")
    for item in results["exclusions"]["rows"]:
        warn(f"quote {item['id']} not a curve input: {item['reason']}")
    manifest = build_manifest(
        results, args.case, args.config, sys.argv[1:] if argv is None else list(argv)
    )
    paths = write_outputs(results, args.out, manifest)
    if args.report is not None:
        from .report import render_report

        args.report.write_text(render_report(results), encoding="utf-8", newline="\n")
        paths["report"] = args.report
    for name, path in paths.items():
        print(f"wrote {name}: {path}")
    return 0
