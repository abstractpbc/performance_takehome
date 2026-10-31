"""Every documented `run.py` command line must parse with the real argument parser."""

from __future__ import annotations

import re
import shlex
import unittest

import run

from . import _paths

DOCS = [
    _paths.HARNESS_DIR / "README.md",
    _paths.HARNESS_DIR.parent / "NOTES.md",
]
ENTRY = "tasks/harness-design/harness/run.py"


def _command_lines(text: str) -> list[str]:
    """Join backslash-continued shell lines inside fenced blocks and keep run.py invocations."""
    out: list[str] = []
    in_block = False
    buffer = ""
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            in_block = not in_block
            buffer = ""
            continue
        if not in_block:
            continue
        line = raw.rstrip()
        if line.endswith("\\"):
            buffer += line[:-1] + " "
            continue
        full = (buffer + line).strip()
        buffer = ""
        if ENTRY in full:
            out.append(full.split("#", 1)[0].strip())
    return out


class DocumentedCommandsParseTest(unittest.TestCase):
    def test_every_documented_run_py_invocation_parses(self) -> None:
        parser = run.build_parser()
        seen = 0
        for doc in DOCS:
            for command in _command_lines(doc.read_text("utf-8")):
                argv = shlex.split(command)
                idx = next(i for i, a in enumerate(argv) if a.endswith("run.py"))
                args = argv[idx + 1 :]
                with self.subTest(doc=doc.name, command=command):
                    try:
                        parser.parse_args(args)
                    except SystemExit as exc:  # argparse reports usage errors via exit
                        self.fail(f"{doc.name}: {command!r} does not parse (exit {exc.code})")
                seen += 1
        self.assertGreaterEqual(seen, 4, "expected documented run.py commands in README/NOTES")

    def test_readme_documents_the_single_test_command(self) -> None:
        text = (_paths.HARNESS_DIR / "README.md").read_text("utf-8")
        self.assertRegex(
            text,
            re.compile(r"unittest discover -s tasks/harness-design/harness/tests"),
        )
