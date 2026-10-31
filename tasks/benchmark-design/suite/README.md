# Candidate benchmark suite — Task 1 (benchmark-design)

> **CANDIDATE-BUILT STAND-INS INSIDE.** The organizer's fixed measurement runner, plan/event
> schemas, fault/control fixtures and enhanced Pi harness are unreleased
> (`docs/RELEASE_STATUS.md`). Everything below that the organizer was going to supply is a
> candidate stand-in with its own directory and README, behind an adapter boundary, so the
> real piece can be dropped in. The organizer's original interface note is preserved at the
> bottom of this file.

The suite answers one question for an OpenAI-compatible streaming server: **is it fast
(TTFT), smooth (Etalon fluidity / FTGR, startup-to-first-valid-generation) and correct
(completion, text validity, independent token accounting)** — and it refuses to score
evidence it cannot trust.

The candidate interfaces are the self-contained execution path; a future organizer
replacement is optional, not a prerequisite. See [EXECUTION.md](../EXECUTION.md) for the
real-target workflow: `workload.efficient.json`, separate baseline calibration,
bounded `capture.py`, independent timing checks, owned-container cleanup and verified
evidence export. The original plan/examples and committed mock reports below remain
historical development evidence. Actual MI355X calibration, quick and full results are
recorded separately in [REAL-RUN-20261028.md](../REAL-RUN-20261028.md), including
their source identities, uncertainty and the completed full population. The linked
public packet contains compressed raw evidence and a CPU-only replay procedure using
CPython 3.14.4, Unicode 16.0.0 and Tokenizers 0.22.2; no organizer release is needed.

`capture.py` launches the attached Docker client through `owned_docker.py`.
Between cold repetitions, the supervisor checks the complete container ID,
capture owner label, name and immutable image, removes that exact container,
and confirms absence before exiting. Stopping the attached Docker client alone
can leave the daemon-managed engine serving, which invalidates a subsequent
cold start. Cleanup failure remains an error; it never becomes a warm startup
measurement. The lifecycle supervisor preserves engine arguments and request population.

Recorder 1.1.1 also handles capture cancellation through the runner's cleanup
path, waits for the owned supervisor to finish, and counts lag intervals that
overlap measurement boundaries. Current source additionally enforces complete
calibration scheduling and rejects nonempty commentary/fences in number-only
outputs while retaining generic whitespace tolerance. These later fixes have
native CPU validation; they were not used for the original GPU captures. The
original filtered lag summary lacks raw intervals, so later replay cannot
retroactively certify boundary coverage. See the dated reanalysis and current
validation receipts in the public packet.

## Layout

| Path | Owner | What it is |
| --- | --- | --- |
| `workload.json` | candidate (deliverable) | versioned declarative plan: cells, prompt sources, arrivals, warmup, cache state, output policy, deadlines, startup procedure, quick/full modes with budget arithmetic, A/A controls |
| `schema/` | candidate stand-in | JSON Schema for per-request and run-level raw events (`schema_version` 1.0.0) + the adapter point for the organizer's real schema |
| `bench/` | candidate | library: plan validation and budget arithmetic (`plan.py`), seeded prompts with checkable expected properties (`prompts.py`), arrivals (`arrivals.py`), task-local tokenizer (`tokens.py`), record loader/validator (`records.py`), per-request gates (`gates.py`), bootstrap/decision statistics (`stats.py`) |
| `runner/` | candidate stand-in | async `httpx` runner: cold start + readiness polling + probe generations, Poisson/closed-loop arrivals, verbatim SSE capture with monotonic timestamps, event-loop lag monitor, `--dry-run` |
| `mock_server/` | candidate stand-in | stdlib-asyncio OpenAI-compatible mock with 18 seeded fault profiles and a token-stream oracle |
| `fixtures/` | candidate stand-in | `generate.py`, the small mock plan `workload.mock.json`, generated `public/<profile>/` (labeled) and `heldout/hx-*/` (opaque) event fixtures |
| `evaluate.py` | candidate (deliverable) | the graded analysis; reads only `run.json` + `requests.jsonl[.gz]` |
| `capture.py` | candidate | Linux suite/allocation deadline, immutable image selection, exact owned-container cleanup, evaluator/audit orchestration and allowlisted hash-verified export |
| `bench/calibration.py` | candidate | separate baseline-only empirical prefill envelope; freezes a new plan/digest before scoring |
| `audit_timing.py` | candidate | independent raw JSON SSE timing/text/completion and startup-linkage consistency; imports no runner/evaluator/metric code |
| `workload.efficient.json` | candidate | pilot-ready plan; separate short/long calibration, all scored load points, repetitions/controls and explicit leading-nonce cache policy |
| `tests/` | candidate | unittest suite covering the validation matrix |
| `../validation/` | candidate | reports per fixture, discrimination table, control false-positive rate, held-out results, measured CPU wall time, integrity output, dry-run estimates |

## Commands (from a clean clone; Python 3.11 via `uv`)

```bash
uv sync --frozen --extra dev
cd tasks/benchmark-design/suite

# 1. Plan validation + cost arithmetic (no server needed)
uv run --frozen python -m runner --plan workload.json --mode quick --dry-run
uv run --frozen python -m runner --plan workload.json --mode full  --dry-run

# 2. Unit tests (one command)
uv run --frozen python -m unittest discover -s tests -v

# 3. Evaluate a captured events directory (fixture shown; any directory with run.json + requests.jsonl works)
uv run --frozen python evaluate.py --events fixtures/public/stall \
    --workload fixtures/workload.mock.json --out /tmp/stall.report.json --summary /tmp/stall.md
uv run --frozen python evaluate.py --events fixtures/public/clean --compare fixtures/public/slow_decode \
    --workload fixtures/workload.mock.json --out /tmp/compare.report.json

# 4. Run the mock + runner end to end (CPU only, ~30 s)
uv run --frozen python -m mock_server --profile burst --seed 7 --port 8089 &
uv run --frozen python -m runner --plan fixtures/workload.mock.json --mode quick \
    --base-url http://127.0.0.1:8089 --out /tmp/events_burst --assume-mock
curl -s -X POST http://127.0.0.1:8089/_mock/shutdown
uv run --frozen python evaluate.py --events /tmp/events_burst --workload fixtures/workload.mock.json --out /tmp/burst.json

# 5. Regenerate fixtures (~20 min on CPU) and rebuild validation artifacts
uv run --frozen python fixtures/generate.py
(cd .. && uv run --frozen python validation/build_validation.py)
```

Against a real engine the same runner is used with the real plan and a cold-start command:

```bash
uv run --frozen python -m runner --plan workload.json --mode full --out <events_dir> \
    --base-url http://<engine>:8000 --launch-cmd "<command that cold-starts the pinned engine>" \
    --tokenizer-snapshot <verified_target_tokenizer_dir> \
    --stop-after-launch --gpu-count 1 --gpu-hours 3 --gpu-kind MI355X
```

### Pinned real output tokenizer (CPU-only preparation)

Real runs now require a verified tokenizer snapshot; regex counting remains available
for explicitly labelled mock captures. No model weights or GPU are needed to prepare
the snapshot. The optional native runtime is isolated from the protected root lock:

```bash
# Use a clean Python environment. --no-deps is intentional: this adapter only uses
# Tokenizer.from_file, which needs no Hub client/Transformers/remote code.
python -m pip install --no-deps --require-hashes -r requirements-tokenizer.txt
python -m bench.model_tokens --model target --out /tmp/abstract-tokenizers/target
python -m bench.model_tokens --model smoke --out /tmp/abstract-tokenizers/smoke
BENCHMARK_TOKENIZER_SNAPSHOTS=/tmp/abstract-tokenizers python -m unittest tests.test_model_tokens -v
```

`tokenizer.lock.json` pins the tokenizer/config bytes and SHA-256 values at the two
protected model revisions (about 24 MB together), with wheel hashes for Tokenizers
0.22.2 on Linux amd64 and Windows amd64. Capture and evaluation perform **no downloads**.
Use `--tokenizer-snapshot /tmp/abstract-tokenizers/target` on **both** runner and
evaluator, including the real-engine example above. The role comes from
`plan.target.model_role`; a smoke plan must select `smoke` and its matching assets.
Wrong files, runtime, model or recorded tokenizer identity fail closed. `run.json`
and reports retain hashes, model revision, runtime, Unicode database and mapping identity.

Counts are the exact pinned tokenizer's **re-encoding of the completed decoded output**,
with special-token insertion disabled. They do not recover the server's generated token
IDs, hidden/stripped special tokens, or chat-template prompt tokens. Prompt construction
and its `approx_prompt_tokens` remain regex approximations; real prefill calibration must
account for that until a trusted prompt/token adapter is available. Content and reasoning
channels are counted separately; their boundary can differ from internal generation.
The existing usage-ratio policy is therefore retained rather than claiming exact equality.

Token end offsets map to the observed SSE delta containing their completed characters.
Both pinned tokenizers normalize with NFC. The adapter waits for the complete original
normalization cluster before assigning a token's arrival, covering combining marks,
canonical reordering and Hangul composition. This can delay a cluster conservatively;
it never invents individual token arrival times. Multiple tokens still share one delta
time and `resolution` remains **delta**. Incremental UTF-8 decoding preserves characters
split across network chunks. Model-internal/per-token timing remains an external gap.

Exit codes: `evaluate.py` returns 0 for `valid`, 3 for `inconclusive`, 4 for `invalid`;
`runner --dry-run` returns 2 when the mode does not fit its wall cap.

## End-to-end execution of the shipped plan (CPU mock)

`validation/quick_mock_run/` is one real execution of `workload.json` in `quick` mode
against the cold-started CPU mock (clean profile, `--time-scale 1.0`), evaluated with the
shipped plan (`report.json`, `summary.md`). Wall time is recorded by
`validation/build_validation.py` into `validation/timing.md`. Reproduce with:

```bash
cd tasks/benchmark-design/suite
PORT=18451; PY=$(uv run --frozen python -c 'import sys;print(sys.executable)')
uv run --frozen python -m runner --plan workload.json --mode quick --out ../validation/quick_mock_run \
  --base-url http://127.0.0.1:$PORT --time-scale 1.0 --assume-mock --stop-after-launch \
  --launch-cmd "$PY -m mock_server --profile clean --seed 7 --port $PORT --time-scale 1.0" \
  --run-id quick-mock-clean-7
gzip -9 ../validation/quick_mock_run/requests.jsonl
uv run --frozen python evaluate.py --events ../validation/quick_mock_run --workload workload.json \
  --out ../validation/quick_mock_run/report.json --summary ../validation/quick_mock_run/summary.md
```

## Fixture size and regeneration

Held-out key: `fixtures/generate.py` writes it to `~/.benchmark-design/heldout_key.json`
(override with `--key-out`), never into the tree; `fixtures/heldout/COMMITMENT.sha256` is
its sha256. `validation/build_validation.py` reads the key from that path or from
`$BENCHMARK_DESIGN_HELDOUT_KEY` and, without it, still rebuilds every observed column and
says "key absent" instead of claiming a match.

`fixtures/public` (20 dirs) + `fixtures/heldout` (13 dirs) total 8.4 MB as `du -sh suite/fixtures` block usage (apparent size may differ), gzip-compressed
(`requests.jsonl.gz`, `oracle.json.gz`; `run.json` and `label.json` plain). No committed
non-fixture file exceeds 1 MB. Regenerate with `uv run --frozen python fixtures/generate.py`
(≈ 12 min CPU at native mock timing; `--only <profile>` for one fixture, `--skip-heldout`),
then rebuild `../validation/` with `(cd .. && uv run --frozen python validation/build_validation.py)`,
which also re-derives the discrimination / control / held-out tables from scratch.
Evidence loading is bounded (`bench/records.py`: 16 MB per record, 256 MB per file
decompressed) so a corrupt or hostile fixture fails fast instead of exhausting memory.

## Evidence rules the suite enforces

* **Timestamps** are `time.monotonic()` from the client process; nothing is back-filled.
  Missing or non-monotonic times make the request `evidence_invalid` and the run `invalid`.
* **Chunks are not tokens.** Every SSE payload is kept verbatim; the evaluator re-parses it
  with `benchmarks.streaming`, concatenates the deltas, re-tokenizes with the task-local
  tokenizer and assigns each token the arrival time of the delta holding its last
  character (resolution `delta`, flagged in every record). Burst deltas therefore produce
  zero intra-burst intervals, which the Etalon fluidity metric penalises correctly.
* **Server `usage` is recorded, never trusted.** A completion count outside
  `token_accounting.usage_ratio_band` of the independent count rejects the request.
* **Completion** requires both a `finish_reason` and the literal `[DONE]`; the text must
  satisfy the prompt's independently checkable expectation (nonce echo, planted fact,
  exact integer sequence, no replacement characters).
* **Startup** is launch → first *valid* completed generation. `/v1/models` readiness is
  recorded and reported as `readiness_lead_s` but never stops the clock.
* **Client lag** (event-loop stalls above `client_integrity.max_event_loop_lag_s` inside a
  measurement window, or a send more than `max_send_lag_s` late) invalidates the run.
* **Population accounting** per unit: planned / offered / completed / failed / unfinished /
  evidence_invalid. Server faults are *scored as failures* (fluidity index 0); evidence
  faults are *not scored at all*.
* **Status** is `invalid` (evidence untrustworthy) > `inconclusive` (too little trustworthy
  evidence, no valid cold launch, underpowered comparison) > `valid`. Missing evidence is
  never a pass.

## Declared interfaces and measurement limitations

| Piece | Organizer-supplied | Candidate-built stand-in | Still missing / needs organizer |
| --- | --- | --- | --- |
| Shared metrics (`benchmarks/metrics.py`) and SSE parser (`benchmarks/streaming.py`) | yes, used unmodified | — | — |
| Engine lock (`baseline/lock.json`), budgets, integrity checker | yes, read only | — | `takehome-start` tag (absent; commit `81aead87…` used) |
| Fixed measurement runner | no | `runner/` (adapter `native`, schema 1.0.0) | the real runner; its output mapped through `bench/records.py::load_events(adapter=…)` |
| Plan / raw-event schemas | no | `workload.json` (`schema_version`), `schema/*.schema.json` | organizer schema; field mapping documented in `schema/README.md` |
| Engine (ZML/LLMD on MI355X) | pinned image/model, not yet executed here | `mock_server/` (injected timings) | real execution remains unmeasured; planning arithmetic is not GPU evidence |
| Fault / control fixtures | no | `fixtures/public`, `fixtures/heldout` from the mock | organizer fixtures; evaluator is identity-independent (tested) |
| Tokenizer for independent counts | no | pinned model-repository assets + `bench/model_tokens.py` output adapter; regex only for mocks/approximate prompts | LLMD parity validation, exact chat-template prompt tokens and true per-token arrival timestamps |
| Prefill deadline curve | no | `bench/calibration.py` fits a separate baseline capture and freezes a new plan, with explicit approximate-length/envelope limitations | actual baseline calibration measurements remain required |
| Enhanced Pi harness, profile digests, receipts | no | — | `UNAVAILABLE_IN_DRAFT` in `submission.json` |

## Adapter boundary

`bench/records.py::load_events(directory, adapter)` is the only code that opens evidence
files. `evaluate.py` sees `RunRecord`/`RequestRecord` objects and nothing else. Adding the
organizer's format means adding one loader and one `ADAPTERS` entry; the gates, metrics and
report schema do not change. Prompt expectations travel inside each request record
(`prompt.expected`), so the evaluator does not need the plan to judge text validity — the
plan supplies deadlines, SLOs, bands and statistics only.

---

## Organizer's original interface note (unchanged text, kept for reference)

The suite is **candidate-owned**. The measurement harness is **organizer-owned**.
This directory is a contract/starter, not an implemented benchmark or runner.
The organizer must supply and validate the plan/raw-event schemas before release.

Task 1 also receives the [enhanced Pi 1.0 coding-agent harness](../../../agent-harness/README.md)
for benchmark authoring/debugging. That harness and the trusted measurement runner are
separate, organizer-owned, and read-only. Neither is a candidate deliverable.

### Separation of responsibilities

1. Candidate supplies a versioned declarative workload/measurement plan (`workload.json`).
   It specifies prompts/seeds, arrivals, output policy, warmup, phases, repetitions,
   allowed cache-state requests, and deadlines within the organizer's schema.
2. The **fixed measurement harness** validates that plan, launches the pinned engine,
   applies isolation/cache policy, drives requests, and records immutable raw events.
   Candidate code cannot substitute client timestamps, token identities, or engine output.
3. Candidate `evaluate.py` consumes the captured events and plan, computes the benchmark's
   metrics/gates/comparison, and writes a structured report. The organizer also invokes
   it against held-out event fixtures. This is analysis, not an engine-optimization loop.

Proposed analysis invocation, from the repository root:

```text
python tasks/benchmark-design/suite/evaluate.py \
  --events <organizer-captured-read-only-event-directory> \
  --workload tasks/benchmark-design/suite/workload.json \
  --out <report.json>
```

### Required report content

- Suite/config/source identity and referenced raw evidence; no hidden label dependency.
- Planned/offered/completed/failed/unfinished requests per cell/repetition.
- Output/quality validity, independent token counts, completion and client-lag checks.
- TTFT, Etalon fluidity/FTGR, and startup distributions with their populations/deadlines.
- Quick/full mode, cache/warmup state, sample sizes, uncertainty, and comparison decision.
- Explicit `valid`, `invalid`, or `inconclusive` results and reasons. Missing evidence is
  not a perfect score or an empty set of passing gates.
- Measured wall time, allocated GPU-hours, and launch/warmup/drain breakdown.

### Supplied validation evidence

Public fixture families cover bursts, stalls, late first tokens, reasoning channels,
Unicode/token-boundary behavior, missing completion, errors/drops, invalid output,
false usage counts, and misleading readiness. Clean/noisy control fixtures are necessary
for false-positive checks. Real driver/token compatibility is tested by the organizer,
not delegated to the candidate as an extra hidden implementation requirement.

Do not change root `tests/`, supplied harnesses, fixtures, baseline, or shared tools.
Candidate tests belong in this directory's `tests/` subdirectory. Run the
[protected-file integrity check](../../../docs/INTEGRITY.md) before submission; improving
your suite by weakening supplied tests is invalid.

The shared metric primitives may be imported as reference utilities. Workload/gate/SLO
choices remain the candidate's design. Nothing here can alter Task 2's fixed benchmark.
