"""Optional pinned HF Tokenizers adapter; reads local data, never model code/weights.

``python -m bench.model_tokens --model target --out /path/to/snapshot`` downloads
only the two small tokenizer assets pinned in tokenizer.lock.json. Evaluation and
serving capture load a verified local snapshot and perform no network requests.
"""

import argparse
import hashlib
import importlib.metadata
import json
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any

from bench import REPO_ROOT, SUITE_DIR

RUNTIME_VERSION = "0.22.2"
LOCK_PATH = SUITE_DIR / "tokenizer.lock.json"


class TokenizerError(ValueError):
    """A snapshot, runtime or token offset cannot support trustworthy evidence."""


def _nfc_boundaries(text: str) -> tuple[str, list[int]]:
    """Map every normalized character to its full original normalization-cluster end.

    Delaying a cluster until all its characters arrive prevents early timing when NFC
    composes/reorders combining marks (HF's raw offsets can omit a trailing mark).
    Adjacent class-zero composition, including Hangul Jamo, is detected explicitly.
    """
    chunks: list[str] = []
    ends: list[int] = []
    cluster = ""
    for index, char in enumerate(text):
        joins = bool(cluster) and (
            unicodedata.combining(char) != 0
            or unicodedata.normalize("NFC", cluster + char)
            != unicodedata.normalize("NFC", cluster) + unicodedata.normalize("NFC", char)
        )
        if cluster and not joins:
            normalized = unicodedata.normalize("NFC", cluster)
            chunks.append(normalized)
            ends.extend([index] * len(normalized))
            cluster = ""
        cluster += char
        if len(cluster) > 4096:
            raise TokenizerError("normalization cluster exceeds 4096 character evidence limit")
    normalized = unicodedata.normalize("NFC", cluster)
    chunks.append(normalized)
    ends.extend([len(text)] * len(normalized))
    result = "".join(chunks)
    if result != unicodedata.normalize("NFC", text):
        raise TokenizerError("normalization-cluster mapping differs from complete NFC output")
    return result, ends


def model_spec(role: str) -> dict[str, Any]:
    """Check candidate asset pins against the protected model revision."""
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    baseline = json.loads((REPO_ROOT / "baseline/lock.json").read_text(encoding="utf-8"))
    if role not in lock["models"]:
        raise TokenizerError(f"unknown tokenizer model role {role!r}")
    spec = lock["models"][role]
    if any(spec[k] != baseline["models"][role][k] for k in ("repository", "revision")):
        raise TokenizerError("tokenizer asset lock differs from protected model pin")
    return spec


class ModelTokenizer:
    """Count completed decoded text with exact pinned tokenizer; offsets are characters.

    These are re-encoded output IDs, not recovered generation IDs. SSE still supplies
    only delta arrival times. Chat-template prompt overhead remains approximate.
    """

    def __init__(self, directory: Path, role: str) -> None:
        spec = model_spec(role)
        try:
            version = importlib.metadata.version("tokenizers")
        except importlib.metadata.PackageNotFoundError as exc:
            raise TokenizerError("install optional requirements-tokenizer.txt first") from exc
        if version != RUNTIME_VERSION:
            raise TokenizerError(f"tokenizers {RUNTIME_VERSION} required, found {version}")
        for name, asset in spec["files"].items():
            path = Path(directory) / name
            if not path.is_file() or path.stat().st_size != asset["bytes"]:
                raise TokenizerError(f"missing or wrong-sized tokenizer asset: {name}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
                raise TokenizerError(f"tokenizer asset SHA-256 mismatch: {name}")
        import tokenizers

        if tokenizers.__version__ != RUNTIME_VERSION:
            raise TokenizerError("loaded tokenizer module differs from pinned package metadata")

        self.backend = tokenizers.Tokenizer.from_file(str(Path(directory) / "tokenizer.json"))
        self.role = role
        self.model = f"{spec['repository']}@{spec['revision']}"
        self.identifier = (
            f"hf-tokenizers:{self.model}:sha256:{spec['files']['tokenizer.json']['sha256']}"
        )
        self.identity = {
            "identifier": self.identifier,
            "model": self.model,
            "runtime": f"tokenizers=={version}",
            "files": {name: asset["sha256"] for name, asset in spec["files"].items()},
            "timing_resolution": "delta",
            "count_semantics": "reencoded-decoded-output",
            "prompt_count_semantics": "regex-approximation-without-chat-template",
            "unicode_database": unicodedata.unidata_version,
            "offset_mapping": "nfc-cluster-end-v1",
        }

    def end_offsets(self, text: str) -> list[int]:
        """One character end offset per token, preserving overlapping byte-fallback spans."""
        try:
            text.encode("utf-8", errors="strict")
            normalized, original_ends = _nfc_boundaries(text)
            encoding = self.backend.encode(normalized, add_special_tokens=False)
            ends = [end for _, end in encoding.offsets]
            if (
                self.backend.encode(text, add_special_tokens=False).ids != encoding.ids
                or self.backend.decode(encoding.ids, skip_special_tokens=False) != normalized
                or len(ends) != len(encoding.ids)
                or any(not 0 <= start < end <= len(normalized) for start, end in encoding.offsets)
                or ends != sorted(ends)
                or (normalized and (not ends or ends[-1] != len(normalized)))
            ):
                raise TokenizerError(
                    "tokenizer cannot round-trip text with valid character offsets"
                )
            return [original_ends[end - 1] for end in ends]
        except (UnicodeError, RuntimeError) as exc:
            raise TokenizerError(f"tokenizer rejected output text: {exc}") from exc

    def count(self, text: str) -> int:
        return len(self.end_offsets(text))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("smoke", "target"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    spec = model_spec(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, asset in spec["files"].items():
        with urllib.request.urlopen(asset["url"], timeout=90) as response:
            raw = response.read(asset["bytes"] + 1)
        if len(raw) != asset["bytes"] or hashlib.sha256(raw).hexdigest() != asset["sha256"]:
            raise TokenizerError(f"download does not match pinned asset: {name}")
        (args.out / name).write_bytes(raw)
    print(
        json.dumps(
            {
                "model": f"{spec['repository']}@{spec['revision']}",
                "files_verified": list(spec["files"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
