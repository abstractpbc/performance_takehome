"""Task-local, dependency-free mock token counting (CANDIDATE-BUILT STAND-IN).

The organizer's token adapter is not released. Until it is, the suite counts tokens by
re-tokenizing verbatim SSE deltas with a deterministic regex pre-tokenizer modelled on
the GPT-2/Qwen byte-level pre-tokenization pattern. It is an *approximation* of the
model's tokenizer: counts are reproducible and independent of the server's ``usage``
field, but they are not the engine's token ids. Every count carries ``TOKENIZER_ID`` so
reports can never be mistaken for model-tokenizer counts.

Real capture/evaluation requires the optional pinned ``bench.model_tokens`` adapter.
This module remains the compatibility path for mock fixtures and approximate prompt
construction; it is never silently substituted for a missing real tokenizer.

Timing resolution: all tokens of a delta are assigned the delta's client arrival time
(``resolution = "delta"``). This is conservative for fluidity: a burst of N tokens in one
delta yields N-1 zero intervals, which Etalon credits as slack, and a stall before the
delta is charged in full.
"""

import re
import unicodedata

TOKENIZER_ID = "regex-pretokenizer-v1 (GPT-2 style pattern, other-letter scripts per character)"
TIMING_RESOLUTION = "delta"

_PATTERN = re.compile(
    r"'s|'t|'re|'ve|'m|'ll|'d"
    r"| ?[A-Za-z]+"
    r"| ?\d{1,3}"
    r"| ?[^\sA-Za-z\d]+"
    r"|\s+(?!\S)"
    r"|\s+",
)


def token_spans(text: str) -> list[tuple[str, int]]:
    """Return pre-tokens and their end offsets in the original, unmodified text."""
    pieces: list[tuple[str, int]] = []
    for match in _PATTERN.finditer(text):
        piece = match.group()
        if any(unicodedata.category(ch) == "Lo" for ch in piece):
            pieces.extend(
                (ch, match.start() + index + 1)
                for index, ch in enumerate(piece)
                if not ch.isspace()
            )
        else:
            pieces.append((piece, match.end()))
    return pieces


def tokenize(text: str) -> list[str]:
    """Split text into pre-tokens; characters in 'other letter' scripts (CJK) count one each."""
    return [piece for piece, _ in token_spans(text)]


def count_tokens(text: str) -> int:
    """Return the task-local token count for ``text`` (0 for empty text)."""
    if not text:
        return 0
    return len(tokenize(text))
