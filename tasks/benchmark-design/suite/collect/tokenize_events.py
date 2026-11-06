"""Derive tokens.jsonl from raw SSE with the pinned tokenizer. Never edits raw files."""

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from benchmarks.streaming import (  # noqa: E402
    StreamProtocolError,
    decode_payload,
    generated_deltas,
)


def token_times(
    pieces: Sequence[tuple[float, str]], ends: Sequence[int]
) -> list[float]:
    """Give each token the time of the event that completed it.

    `pieces` is (receive time, text) per event for one channel. `ends` is each token's end
    offset in the joined text. Tokens that one event completes share its timestamp.
    """
    times, cursor, reached, last = [], 0, 0, 0.0
    for end in ends:
        while reached < end and cursor < len(pieces):
            last, text = pieces[cursor]
            reached += len(text)
            cursor += 1
        times.append(last)
    return times


def _channels(events: list[dict[str, Any]]) -> dict[str, list[tuple[float, str]]]:
    channels: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for record in sorted(events, key=lambda item: item["seq"]):
        try:
            event = decode_payload(record["data"])
            deltas = [] if event is None else generated_deltas(event)
        except StreamProtocolError:
            continue
        for channel, text in deltas:
            channels[channel].append((record["t"], text))
    return channels


def tokenize_request(request_id: str, events: list[dict[str, Any]], tokenizer) -> list[dict]:
    tokens = []
    for channel, pieces in _channels(events).items():
        encoding = tokenizer.encode(
            "".join(text for _, text in pieces), add_special_tokens=False
        )
        times = token_times(pieces, [end for _, end in encoding.offsets])
        tokens += [
            {"t": t, "channel": channel, "token_id": token_id}
            for t, token_id in zip(times, encoding.ids, strict=True)
        ]
    tokens.sort(key=lambda token: token["t"])
    return [{"request_id": request_id, "index": index, **token}
            for index, token in enumerate(tokens)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(
        hf_hub_download(args.repository, "tokenizer.json", revision=args.revision)
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with (args.events / "sse.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            grouped[record["request_id"]].append(record)
    with (args.events / "tokens.jsonl").open("w", encoding="utf-8") as out:
        for request_id, events in grouped.items():
            for token in tokenize_request(request_id, events, tokenizer):
                out.write(json.dumps(token) + "\n")
    (args.events / "tokens.meta.json").write_text(json.dumps({
        "tokenizer": args.repository, "revision": args.revision, "tool": "tokenize_events.py",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
