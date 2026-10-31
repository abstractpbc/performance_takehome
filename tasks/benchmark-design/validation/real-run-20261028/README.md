# Completed real captures and CPU replay — 28 October 2026

This packet contains the complete candidate calibration, quick and full raw
captures, not a synthetic replacement. Start with
[the result table and limitations](../../REAL-RUN-20261028.md).
[results.json](results.json) summarizes populations and A/A diagnostics;
[provenance.json](provenance.json) distinguishes the actual capture source
`7d6fd18`, external full-run lifecycle adapter, and separately tested canonical
code `ea97cb8`. Their evaluator and independent-audit bytes matched the capture
source. Current combined source is `331c467`, with the later fixes and validation
described below. No organizer runner, unreleased harness, API key, model weights
or GPU is needed to replay the evidence.

The three `.tar.gz` files are the original inner exports, with no edits or
recompression. Each contains the complete `events/run.json` and
`events/requests.jsonl`, frozen workload, report, independent timing audit, logs,
ownership record, capture receipt and per-member hash manifest. The expanded full
request file is 105,465,565 bytes, so it is deliberately distributed compressed.
The archive itself is 9,459,728 bytes. Do not add expanded requests to Git.

The phase directories expose report, summary, audit, workload and receipt copies
for review. Their `completion.json` files were retained outside the original
inner archive: the final elapsed clock and archive hash are written after export.
`manifest.json` hashes every published packet file except itself. Its archive
records also give original member sizes and hashes. The independently verified
outer full transfer envelope is identified in provenance; it is redundant for CPU
replay and is not part of this public packet.

The calibration `fit.json` and `frozen-workload.json` are original outputs.
All scoring requests use frozen plan SHA-256
`fb82d603967046ab14f895d1bc5be4e681cc25f341a3cc31ce76ca9268edc412`.
Calibration is a separate population. Earlier October 26 evidence and the first
October 28 full failure retain their original histories; none are pooled into
the completed full run. The first full attempt had no measured requests.

The two `.py.txt` files preserve the exact external lifecycle adapters used for
the completed full capture, including their original bytes. They are evidence,
not the current implementation. Current `suite/owned_docker.py` separately fixes
cancellation during late creation; recorder 1.1.1 also handles cancellation
through runner cleanup and measurement-boundary lag intervals. Its
[current native receipt](native-validation-331c467.json) covers exact `331c467`:
214 candidate tests passed, one original held-out-key check skipped, 40 root tests
passed, locked Ruff 0.16.10 and protected integrity passed. Both cached tokenizer
snapshots were verified. The [earlier receipt](native-validation.json) covers
`ea97cb8` and remains historical evidence.

## Later review and reanalysis

Current code rejects nonempty prose/fences in number-only outputs, checks all
planned calibration units, and retains failed-start diagnostics. Blank lines,
surrounding line whitespace and decimal leading zeros remain generically
normalized; there are no exceptions for historical request IDs. Lifecycle and
lag fixes are CPU-tested without a new GPU capture.

Source `556e7b8` reanalyzed the unchanged captures at **19:34:42–19:35:31 UTC**
with the pinned runtime. The [dated derived receipt](post-review/receipt.json)
and three derived reports retain that later identity. All metrics, populations
and verdicts match originals except local paths, and there are no additional
numeric-format failures. The calibration fit and frozen plan reproduce byte for
byte. Original reports, archives and their hashes remain unchanged. The later
reanalysis and source QA are not part of the original timed GPU execution.

**Historical lag limit:** the original full record has filtered maximum lag
8.51089 ms but no raw lag intervals or unfiltered global maximum. It cannot rule
out omitted boundary-crossing stalls. Current recorder 1.1.1 fixes the observation
rule for future captures. A derived report's `valid` status describes checks on
stored evidence; it cannot recreate missing historical lag evidence or certify
that capture against the repaired boundary gate.

## Verify and replay

Run from `tasks/benchmark-design` in a clone of
`codex/task1-real-run-final`. Use **CPython 3.14.4, Unicode database 16.0.0 and
Tokenizers 0.22.2**. Unicode version is part of the tokenizer identity; another
version should refuse unchanged raw evidence. Use a fresh output directory with
at least 200 MB free. These commands read the evidence and write new replay
outputs; they do not contact an inference server.

```sh
python3.14 -m venv /tmp/task1-replay-venv
. /tmp/task1-replay-venv/bin/activate
python -m pip install --no-deps --require-hashes -r suite/requirements-tokenizer.txt
python -c 'import sys, unicodedata, tokenizers; assert sys.version_info[:3] == (3, 14, 4); assert unicodedata.unidata_version == "16.0.0"; assert tokenizers.__version__ == "0.22.2"'

PACKET='validation/real-run-20261028'
OUT="$(mktemp -d)"
TOKENIZER="$OUT/target-tokenizer"
# Downloads only the two public hash-locked tokenizer files (about 13 MB).
PYTHONPATH=suite python -m bench.model_tokens --model target --out "$TOKENIZER"

for PHASE in calibration-direct-1 quick-direct-1 full-direct-2; do
    ARCHIVE="$PACKET/$PHASE.tar.gz"
    python -B suite/capture.py verify-export "$ARCHIVE" 150000000
    RAW="$OUT/$PHASE"
    mkdir "$RAW"
    tar -xzf "$ARCHIVE" -C "$RAW"
    python -B suite/evaluate.py --events "$RAW/events" --workload "$RAW/workload.json" \
        --tokenizer-snapshot "$TOKENIZER" --out "$RAW/replayed-report.json"
    python -B suite/audit_timing.py --events "$RAW/events" --out "$RAW/replayed-audit.json"
done

PYTHONPATH=suite python -B -m bench.calibration \
    --plan "$OUT/calibration-direct-1/workload.json" \
    --events "$OUT/calibration-direct-1/events" --tokenizer-snapshot "$TOKENIZER" \
    --out-plan "$OUT/replayed-frozen-workload.json" --out-receipt "$OUT/replayed-fit.json"
cmp "$OUT/replayed-frozen-workload.json" "$PACKET/frozen-workload.json"
cmp "$OUT/replayed-fit.json" "$PACKET/fit.json"
```

The archive verifier checks all original member sizes and hashes before
extraction. Compare replayed reports semantically after removing the
`events_dir` field, which reflects the new local path. The independent audit
must report `PASS`; the reports must preserve the recorded populations,
failures, startup values, metrics and A/A decisions. Keep original reports
unchanged even if a different runtime refuses or produces different results.

The actual report tokenizer identity uses re-encoded decoded output with
delta-level arrival timestamps and NFC-cluster offset mapping. Prompt lengths
remain an approximate user-message covariate, not exact engine chat-template
token IDs. The fit is an observed envelope, not a prediction interval.

## Runtime and resource boundary

Full capture completion records 7,572.170621909687 seconds from original T0
`2026-10-28T16:10:34.243406+00:00`. This includes prior preparation,
calibration, quick, failed-full-attempt time, full scoring, exact container
cleanup and export. It is below 10,800 seconds, without resetting the clock.
The separate [resource closure receipt](resource-closure.json) confirms an
identity-verified DELETE (204), independent GET (404), and canonical GPU absence
at **18:30:02.437658 UTC**: 8,368.194252 seconds (2h19m28.194252s) from the same
original T0. Its offhost verification at 18:30:35.405337 UTC is also within the
three-hour ceiling. Later source QA/reanalysis does not redefine this clock.

The allocation was shared with Task 2 from 16:21:22 UTC until confirmed absence.
At the stated $4.50/GPU-hour base ceiling, its upper elapsed-time estimate is
$9.66; the provider invoice is pending. This is not Task 1 exclusive spend.

A brief CPU-only metadata build overlapped full capture at
`2026-10-28T17:57:56.621956+00:00`–
`2026-10-28T17:57:59.589754+00:00`. Perfect host isolation is not claimed.
Full A/A is inconclusive overall; neither statistical power nor configuration
speedup nor an organizer grade is established.
