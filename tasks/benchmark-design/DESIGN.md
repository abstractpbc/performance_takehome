# Task 1 — Benchmark design report

**Current result:** calibration, quick and full development captures completed on
one MI355X with Qwen3.6-27B and a digest-bound LLMD wrapper. Calibration had 60
measured requests; quick completed 242/242; full completed 1,453/1,455 with two
retained nonce-validation failures. All evidence and timing audits passed.
Full capture/export completion was 7,572.171 seconds from the original start,
including prior setup, calibration, quick and a failed initial full attempt.
Separate GPU absence was confirmed at 18:30:02.437658 UTC, 2h19m28.194252s
after the original start, with offhost receipt verification also within three hours.
See [actual results and limitations](REAL-RUN-20261028.md) and the
[complete public replay packet](validation/real-run-20261028/README.md).
Full A/A is inconclusive overall: fluidity, minimum-decode p99 and TTFT p90
report no difference; TTFT p50 remains inconclusive. No measured power,
configuration speedup or organizer grade is claimed.

The original recorder's filtered lag summary cannot rule out stalls crossing
measurement-window boundaries because raw lag intervals were not retained.
Later source fixes and CPU checks do not retroactively certify that missing
evidence. A separately dated 19:35 quality/calibration reanalysis reproduces all
original metrics and verdicts; original archives/reports remain unchanged.
For the original 28 October full capture, recorder 1.1.1 and source `331c467`
were later CPU-tested changes, not the code used by that historical capture.
Recorder 1.1.1 now also has the separate actual fixed90 validation below.

A [separate fixed90 real-engine supplement](validation/engine-fixed90-supplemental-20261030/README.md)
now exercises recorder 1.1.1 with 30 clean A, 30 injected 100 ms, and 30 clean B
requests. All 90 completed under the current 50 ms lag gate; the recorded delay
was 100.073 ms (descriptive 95% CI 99.632–100.526 ms). This later work is outside
the primary three-hour claim. It is not a full-suite rerun or measured-power
result; the original evaluator remains inconclusive and historical files/clocks
stay unchanged.

[EXECUTION.md](EXECUTION.md) describes the self-contained candidate recorder,
calibration/freeze, bounded capture, export and cleanup. The organizer's future
runner is not needed to replay these supplied results. The original plan and
CPU mock results in sections 1–6 below are historical evidence. Their planned
GPU durations remain arithmetic and their measured mock numbers remain CPU
results. The actual efficient plan was calibrated separately and frozen before
fresh quick/full captures; it is included in the public packet. Earlier October
26 calibration and failed attempts retain their own dates and source identities.

Source identities: suite version `1.0.0` (`suite/bench/__init__.py`), plan
`abstract-t1-llmd-mi355x-v1` schema `1.0.0`, event schema `1.0.0`, report schema `1.0.0`.
The organizer's `takehome-start` tag does not exist in this draft repository; the frozen
starter is commit `81aead87d78ed468c4c374fb42921471b44c3f80` and the integrity check was run
against it (`validation/integrity.txt`).

## 1. Evaluation goals and workloads

The benchmark answers: *is the server fast, smooth and correct for the user experiences it
will actually serve?* Three axes are reported separately, never blended into one score:

| Axis | Statistic | Where computed |
| --- | --- | --- |
| Fast | TTFT = first non-empty **content** delta − send, per cell × load; p50/p90 with bootstrap CIs, p99, prefill-deadline hit rate, TTFT-SLO hit rate | `evaluate.py::Evaluator.cells` |
| Smooth | Etalon fluidity index per request at three decode deadlines, SLO fraction, minimum decode deadline p50/p99, FTGR = 1/p99(min decode deadline) | `benchmarks.metrics` via `evaluate.py` |
| Startup | launch → first **valid** completed generation per cold launch; readiness lead time reported, never used as the stop | `evaluate.py::Evaluator.startup` |

### Cells (`workload.json` → `cells`)

| Cell | Represents | Prompt source | Arrival | Why |
| --- | --- | --- | --- | --- |
| `chat_short_open` | interactive chat: short prompts (64–256 tokens), ≤256 output tokens, nonce echo required | `chat_short` | open-loop Poisson 0.5 / 1 / 2 / 4 rps, 40–120 requests per rate | TTFT and fluidity under increasing arrival pressure; Poisson because chat users arrive independently of server speed (open loop exposes queueing that closed loop hides) |
| `rag_long_open` | RAG / summarisation: 2 048–4 096-token prompts, ≤128 output tokens, planted fact must be returned | `rag_long` | Poisson 0.25 / 0.5 rps, 30–45 requests | prefill-dominated; TTFT must be judged against a prompt-length-dependent deadline (Etalon §4.1) and correctness is checkable (the fact is unique per prompt) |
| `generation_long_open` | long outputs: list 1..N (N drawn per prompt), ≤1 024 tokens | `generation_long` | Poisson 0.2 rps, 30 requests | decode-dominated; exact integer sequence makes truncation, garbling and token-boundary errors detectable without a model judge |
| `mixed_open` | realistic mix 50 % chat / 30 % RAG / 20 % long generation | `mixed` | Poisson 1 rps, 60 requests | interference between prefill-heavy and decode-heavy requests is where stalls appear |
| `closed_loop_sweep` | capacity curve | `chat_short` | closed loop, concurrency 1 / 4 / 16 / 32, 16–96 requests | closed loop bounds offered load by the server's own speed, so it shows the latency/throughput knee without building an unbounded queue; the two arrival models are complementary, not redundant |
| `prefill_calibration` | fits the prefill deadline curve | `rag_long` forced to 128/512/1024/2048/4096 prompt tokens | closed loop, concurrency 1, 15 requests | Etalon §4.1 derives the prefill deadline from isolated prompt-length measurements; the shipped intercept/slope are placeholders to be replaced from this cell |
| `aa_control_a` / `aa_control_b` | identical configuration, different arrival seeds | `chat_short` | Poisson 1 rps, 60 requests each | A/A control: the comparison machinery must call these "no difference" or "inconclusive", never "regression" |

Omitted on purpose: multi-turn conversations (prefix-cache dependent; cache state is
controlled instead), tool calling, images, non-greedy sampling (temperature is pinned to 0
so text validity is deterministic), and any "quality" judged by another model.

### Arrival rates

The historical rates were chosen to fit the 3-hour cap in unmeasured planning arithmetic
(see §5), with three repetitions spanning from an unloaded server (0.5 rps chat, ~1 request in flight)
to a saturating one (4 rps chat ≈ 8–10 concurrent decodes at 30 tok/s per stream, and
closed-loop 32). These are historical planning values. The completed October 28
captures use the frozen efficient plan and retain every scored arrival rate.

### Prompts

Prompts are generated by `bench/prompts.py` from `(prompt_seed, source, cell, repetition,
index)` with SHA-256-derived RNGs, so every request is reproducible and no two requests
share a prefix (each carries a unique nonce), which is the fallback cache policy when the
engine offers no prefix-cache flush (`cache_state`). Each prompt carries an **expected**
block the evaluator checks against the generated text: nonce echo + minimum length (chat),
planted fact (RAG), exact integer sequence (generation), no U+FFFD / mojibake (all).

### Startup procedure

`startup.procedure`: cold-start the pinned engine (clock starts at launch) → poll
`/v1/models` every 1 s and record every probe → issue a short probe generation every 2 s →
stop the clock at the first probe that passes **every evaluator gate**
(`runner/run.py::_is_valid_generation` calls `bench.gates.judge`). Readiness is recorded
as `readiness_at`/`readiness_lead_s` and is informational.

## 2. Metrics and validity

**Startup metric definition.** `startup_to_first_valid_generation_s` = (client `end`
timestamp of the earliest startup probe whose stream passes *every* gate) − (launch
timestamp). The clock stops at the probe stream's **end**, not at its first token, because
validity (finish reason, `[DONE]`, expected text, usage consistency) is only known when the
stream completes; the metric therefore includes the probe's full decode and depends on the
probe's `max_tokens` (32 in the shipped plan). Readiness (`/v1/models` 200) is reported
separately as `first_ready_probe_s`; the gap is `readiness_lead_s`. The per-launch `status`
field copied from `run.json` is informational only — the evaluator never trusts it.
The aggregate startup sample contains only cold launches with a verified completed
generation. Warm-attach timings and failed launches remain visible per launch; they
cannot replace a planned cold-generation observation. Both the recorded launch count
and the verified cold-generation count must equal the planned startup count; missing
observations or extra retries make the run inconclusive. All observed launch timings
remain in the diagnostic report, including retries, without changing that fixed cohort.

### Deadlines (Etalon, arXiv 2407.07000 §4)

* **Prefill deadline** `D_p(prompt_tokens) = max(floor, intercept + slope × tokens) +
  scheduling_slack` (`deadlines.prefill`): Etalon §4.1 defines the prefill deadline as a
  function of prompt length and recommends fitting it from isolated measurements rather
  than using one global TTFT target. Shipped constants (0.25 s + 0.5 ms/token, floor 0.3 s,
  slack 50 ms) are explicitly marked placeholders in the plan; `prefill_calibration` exists
  to refit them.
* **Decode deadlines** (`deadlines.decode_tiers`): **strict 25 ms** (Etalon §5 evaluates a
  25 ms TBT SLO, 40 tok/s), **medium 50 ms** (20 tok/s, interactive reading with headroom),
  **relaxed 166.7 ms** (6 tok/s ≈ 250 words/min, the reading-speed reference in Etalon
  §2.2). Fluidity is computed with Etalon's slot semantics via `benchmarks.metrics.fluidity`:
  early tokens accumulate slack, a long stall misses several consecutive slots, and the
  deadline sequence restarts after a miss.
* **Fluidity SLO** (`deadlines.fluidity_slo`): a request is fluid when its index ≥ 0.9 (≤10 %
  missed slots); the cell meets the SLO when the 99th-percentile request is fluid. FTGR is
  `benchmarks.metrics.fluid_token_generation_rate` = 1/p99 of the per-request minimum
  decode deadline at 10 % miss rate; it is `null` with a reason when any trace is
  unresolved (all tokens in one chunk), never infinity.
* **TTFT SLOs** (`deadlines.ttft_slo_s`): 1 s chat/generation, 4 s RAG, reported as hit
  rates next to the prefill-deadline hit rate; both are descriptive, the gates below are
  what make a run invalid.

### Token accounting

Server `usage` is kept in every record and never used as the count. The independent count
re-tokenizes the concatenated verbatim deltas with the task-local regex pre-tokenizer
(`bench/tokens.py`, id `regex-pretokenizer-v1 …`, resolution `delta`: all tokens in a delta
get the delta's arrival time). This is an approximation of the engine tokenizer and is
flagged as such in every record (`client_tokens.resolution`). Consequences: (a) token
inter-arrival within one delta is 0, so bursts are penalised, never rewarded; (b) the usage
band is wide (0.5–2.0) because the mock's own tokenizer already differs 2× on
newline-separated integers; a 3× false usage still fails. Replacing the tokenizer with the
pinned engine tokenizer (or per-token timestamps from the organizer's adapter) tightens
the band and changes the resolution flag to `token` — nothing else changes.

### Gates (`bench/gates.py`)

| Gate | Outcome | Category |
| --- | --- | --- |
| schema violation, timestamp missing / non-finite / negative / non-monotonic, record text ≠ verbatim payloads, workload digest mismatch, oversized or truncated evidence file, send later than `planned_send + max_send_lag_s` | request `evidence_invalid` → **run invalid** | evidence |
| HTTP ≠ 200, protocol violation (bad JSON, data after `[DONE]` or after the usage chunk, chunks from more than one completion id, multiple choices, non-`text/event-stream` response), empty output, text fails expected properties, `finish_reason` not allowed, usage outside band | request `failed` (scored as failure, fluidity 0) | server |
| no `[DONE]`, no `finish_reason`, connection drop, timeout | request `unfinished` (scored as failure) | server |
| run-level: status ≠ completed, event-loop lag > 50 ms in a measurement window, lag not measured, engine identity missing, A/A controls confidently different, claimed-valid startup probe fails gates | **run invalid** | evidence |
| any planned measurement unit has fewer offered request records than planned, including a completely absent cell | **run inconclusive** (incomplete population evidence) | sufficiency |
| fewer than `min_requests_per_cell` trustworthy requests in a cell, no cold launch, no launch reached a valid generation, no cell with enough evidence | **run inconclusive** | sufficiency |

The report's `status` is `invalid` if any evidence gate fired, else `inconclusive` if any
sufficiency gate fired, else `valid`. A valid run can still describe a terrible server
(e.g. 100 % failed requests) — that is a conclusive negative result, not an invalid one.

## 3. Statistical policy

**Threat model — what the evaluator cannot do.** `evaluate.py` enforces schema, internal
consistency (monotonic client clocks, verbatim payloads vs recorded text, independent token
counts vs usage, startup derived from probe records and cross-checked against `run.json`)
and bounded, deterministic processing. It **cannot detect a coherent rewrite of the whole
evidence directory**: a forger who rewrites the probe records *and* `run.json` consistently
(or re-times every request) produces evidence that is internally consistent and will be
scored. Evidence has no external anchor (no signed clock, no server attestation). The
defence is provenance, which belongs to the organizer: the organizer owns the fixed runner,
runs it on their host, and ships the raw events; the candidate suite only consumes them.
Any run whose events were not produced by the organizer's runner is out of scope for the
verdict.

* **Warmup**: 4 unscored requests per unit (`phase=warmup`), recorded so the population
  table still accounts for them. **Cache state**: flush request before each unit when the
  engine supports it (`--cache-flush-url`), otherwise nonce isolation; the choice is
  recorded in the phase marker.
* **Repetitions**: full mode runs every unit three times, interleaved
  (`modes.full.interleave`) so drift affects all cells alike; quick mode once. Units are
  pooled per cell × load for reporting; the per-unit population table keeps the split.
* **Sample sizes**: `statistics.min_requests_per_cell = 30` trustworthy requests per cell ×
  load in the real plan (12 in the compact mock plan). Below that the cell is marked
  `enough_evidence=false` and the run is inconclusive; latency statistics are still
  reported with their own `n` and CI so partial evidence is visible, not hidden.
* **Uncertainty**: seeded percentile bootstrap (2 000 resamples, 95 %) on TTFT p50/p90,
  fluidity index mean and SLO fraction; inputs are sorted first so record order cannot
  change the CI. **Client lag** evidence is bounded explicitly rather than modelled.
* **Decision rule** (`bench/stats.py::decide`, two runs or A/A within one run): bootstrap
  CI of the difference B − A; *regression* only if the CI excludes 0 on the bad side **and**
  the point estimate exceeds `min_effect` (10 % TTFT p50, 15 % TTFT p90, 0.05 fluidity
  index, 10 % min-decode-deadline p99); *no_difference* if the whole CI lies within
  ±min_effect; otherwise *inconclusive* (underpowered). Any side with fewer than
  `min_requests_per_cell` samples is inconclusive by construction (this is what protects
  against degenerate bootstrap CIs on tiny samples). Confidence is Bonferroni-adjusted
  across **four metrics times the number of compared cells**, including all four metrics
  in a single A/A comparison. This is a nominal correction on percentile-bootstrap
  intervals, not a proven finite-sample family-wise guarantee: shared queueing,
  repetition correlations, and sparse bootstrap tails still require real pilot/control
  calibration. Runs that are not both valid cannot be compared (`invalid`).

**Evidence boundary hardening (independent follow-up review).** The reader rejects NaN,
infinity, overflowing JSON numeric literals and structurally invalid nested records before
analysis, and bounds run metadata to 16 MiB as well as bounding request evidence. Both a
finite lag maximum and actual lag samples are required. For quick/full modes, declared
unit counts/identities, prompt source and deadline profile are checked against the resolved
workload plan; an omitted planned
unit remains visible and makes the result inconclusive, even if omitted from `run.json`.
Missing planned warmup requests also make the result inconclusive.
Compact fixture mode permits partial synthetic cell sets for isolated gate tests. Request
identity must agree with declared unit metadata in every mode. Startup uses only actual
startup-phase probes sent after the corresponding launch, never reused across launches;
missing planned launch repetitions are inconclusive. Historical committed captures/reports
are retained as evidence of the earlier implementation, not silently regenerated.

## 4. Validation matrix (CPU mock fixtures — not GPU or ZML/LLMD)

**How "detected" is decided (round-2 review F5/F6).** `validation/build_validation.py` marks a
fixture detected only when (i) the top-level status equals the expected one **and** (ii) every
machine-checkable predicate listed for that profile in `suite/fixtures/expectations.py`
passes — a named gate with a minimum count, a metric ratio against the clean control, a
startup field, or a reason string. Generic "something moved vs clean" is no longer enough.
Weak signals are not relied on: `slow_startup` must show `first_ready_probe_s` > 3× clean
*and* startup-to-first-valid > 2× clean; `misleading_readiness` must show a readiness probe
as fast as clean (< 2×) *plus* ≥ 1 failed generation probe before the first valid one *plus*
readiness lead > 1 s — which is what separates it from `stall`, whose readiness lead is also
long but whose probes complete slowly without failing. The held-out set has mixed expected
outcomes (valid / invalid / inconclusive) with specific checks; its key is kept outside the
public tree (`~/.benchmark-design/heldout_key.json`, sha256 committed as
`suite/fixtures/heldout/COMMITMENT.sha256`), so "held-out" means *not labelled in this
tree* — the author generated it and knows the answers; it is not blind to the author.

**Specificity is family-level, not profile-level, for three groups.** The cross-matrix test
(`tests/test_expectations.py::test_fault_predicates_are_specific`) records which *other*
profiles also satisfy each profile's predicates; every such overlap must be declared in
`expectations.py` (`ambiguous_with`). The declared overlaps are: completion faults
(`missing_done`, `missing_finish_reason`, `drop` — all end as `unfinished` with the same
gates), invalid-output faults (`wrong_text`, `garbled_text`, `truncated_text` — all fire
`invalid_output`; `drop`'s truncated streams satisfy the garbled/truncated predicates too), decode faults (`stall`, `slow_decode`, and `unicode_split`'s per-character
deltas also slow decode), and TTFT shifts (`late_first_token`, `reasoning_channel`). Within
a group the evaluator *rejects or measures correctly* but does not name the root cause; that
is by design — the suite grades the server's behaviour, not the mock's label.
`unicode_split` carries its own positive signal (`token_evidence.grapheme_boundary_splits`).

Fixtures are produced by the CPU mock (`suite/mock_server`, 18 profiles) through the real
runner with a real cold launch per fixture, at the mock's native injected timings
(`fixtures/manifest.json: time_scale = 1.0`). Two evidence-corruption families are derived
from the clean capture by `fixtures/generate.py::mutate`. Held-out fixtures reuse ten
profiles with fresh seeds under opaque names; `evaluate.py` never reads labels or names
(`tests/test_fixtures.py::FixtureIdentityIndependenceTests` renames, permutes and plants a
decoy label, then asserts a byte-identical report).

The machine-generated discrimination table, control false-positive accounting and
held-out results are in `validation/discrimination.md` (JSON next to it); per-fixture
reports are in `validation/reports/`. Summary of what was observed:

* **20/20 public fixtures** produce the expected top-level status and a machine-derived
  detection signal (`validation/discrimination.json`). Server-fault families are caught by
  gates (rejected requests appear in `population` and `rejection_reasons`): drop, false_usage, garbled_text, http_error, missing_done, missing_finish_reason, timing_invalid, truncated_text, wrong_text.
  Timing families move the metric they should: `late_first_token` TTFT p50, `stall` and
  `slow_decode` strict fluidity and minimum decode deadline p99, `misleading_readiness` and
  `slow_startup` readiness lead time. `burst` is flagged through `multi_token_deltas`
  (chunk ≠ token) while its Etalon fluidity stays 1.0 — by design, since 8-token bursts at
  ~83 tok/s sit inside the playback slack. The two evidence-corruption families
  (`timing_invalid`, `client_lag`) make the run `invalid`, not scored.
* **Control false-positive rate 0.00** (0/4 controls: public clean/noisy and the two held-out
  controls) — no rejected request, status `valid`, within-run A/A decisions
  ['inconclusive', 'no_difference'] (`validation/controls.json`).
* **Held-out set: 13/13 opaque fixtures** (mixed expected outcomes: 10 `valid` mock profiles, `timing_invalid` and `client_lag` → `invalid`, `underpowered` → `inconclusive`) pass their status and every named check from the out-of-tree key
  (`validation/heldout_results.json`; the key is read only by the validation builder).
* **Two-run comparisons** (`validation/comparison_*.json`): clean vs slow_decode →
  `regression`, clean vs late_first_token → `regression`, clean vs noisy →
  `regression` on TTFT p90 only — correct, because the noisy profile really has a 60 % jitter
  tail; the *within-run* A/A cells of the noisy run are the noise control and stay
  non-confident. Comparisons with fewer than `min_requests_per_cell` samples on either side
  are inconclusive by construction.
* **Known masking**: `false_usage` (3×) is caught on chat cells but not on integer-list
  outputs, where the regex pre-tokenizer counts 2× the mock tokenizer and 3× lands inside
  the 0.5–2.0 band. The oracle-agreement test is restricted accordingly; the fix is the
  pinned tokenizer (§2). Startup `readiness_lead_s` also grows for fault profiles whose
  faults hit the probe generation itself (e.g. `stall`), which is real behaviour of a
  faulty server rather than a startup-axis detection.

**False negatives / limitations observed honestly:** the mock's `stall` and `burst`
families are invisible to fluidity when fixtures are generated with compressed timing
(`time_scale 0.25`, tried first): a 3 ms decode interval against a 25 ms deadline builds
enough Etalon slack to absorb a 0.4 s stall. That is correct metric behaviour, not a
detector weakness, and it is why fixtures are generated at native timing. Hidden organizer
fixtures with gentler faults than these may legitimately produce `valid` reports with
small metric shifts rather than gate rejections; the table lists which signal carries each
detection so that outcome is interpretable.

## 5. Measured compute cost

The original October 28 capture/export completed after 126.203 minutes from
the preparation start. Independent resource absence completed after 139.470
minutes, below the 180-minute execution ceiling. The shared allocation's
16:21:22–18:30:02.437658 UTC interval gives a $9.66 upper base estimate at
$4.50/GPU-hour; the provider invoice is pending and the allocation also served
Task 2. See the [resource receipt](validation/real-run-20261028/resource-closure.json).
Later source QA and 19:35 reanalysis are separate CPU-only work, outside the
original execution clock. The arithmetic below remains historical planning.

Quick-mode cap note: the plan's `planned_wall_minutes_max` for quick (45 min) is *planned +
margin by construction* — it was raised from 40 when the per-cell request floor added 52
requests (round-2 review F7). The binding constraint is the organizer's 180-minute suite
ceiling. The historical arithmetic leaves 20 minutes; this is not measured head-room or
proof that model/dependency setup, capture, analysis and export fit that ceiling.

### Planned GPU cost (arithmetic, unmeasured — `validation/dry_run_*.json`)

`runner --dry-run` sums, per unit: setup (10 s) + cache flush (5 s) + warmup + arrival span
(requests/rate for Poisson; requests × service time / concurrency for closed loop) + the
service time of the last request + 30 s drain grace; plus per launch the planning cold
start (480 s) + 60 s; plus 60 s teardown; × 1.25 safety margin. Planning assumptions
(27B model, one GPU): 4 000 prefill tok/s, 30 decode tok/s per stream, 900 aggregate, 80 %
of `max_tokens` produced.

| Mode | Units | Offered requests (+warmup) | Launches | Raw minutes | ×1.25 planned minutes | Cap | GPU-hours (1 GPU) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| quick | 8 | 242 (+28) | 1 | 31.7 | **39.6** | 45 | 0.66 |
| full | 45 | 2541 (+96) | 2 | 128.0 | **160.0** | 180 | 2.67 |

These historical assumptions predict a run within the 3-hour suite ceiling
(`docs/BUDGETS.md`) and proposed 4 GPU-hour grading allowance (`budgets.toml`). They do not
establish actual feasibility. The completed October 28 run instead uses the frozen
efficient plan. Its measured clock includes prior setup, calibration, quick,
failed-attempt time, analysis, exact container cleanup and export; transfer and allocation
deletion are tracked separately. See `REAL-RUN-20261028.md` for the measured result.
Calibration, quick and full remain separate captures, never pooled as one population.

### Measured CPU/mock cost

The single source of truth is `validation/timing.md` / `validation/timing.json`, written by
`validation/build_validation.py` on every run (fixture-generation wall time from
`fixtures/manifest.json`, `evaluate.py` wall time over every fixture, and the candidate
unittest wall time and result line). Numbers are deliberately not retyped here so they
cannot drift from the artifact. `validation/quick_mock_run/` holds the one end-to-end
execution of the shipped `workload.json` quick mode against the CPU mock (§6).

These measure the candidate tooling only.

## 6. Reproducibility

```bash
git clone https://github.com/sharonbasovich/performance_takehome.git
cd performance_takehome && git checkout codex/task1-real-run-final
uv sync --frozen --extra dev
uv run --frozen ruff check .
uv run --frozen python -m unittest discover -s tests -v              # root (organizer) tests
uv build
uv run --frozen python -B -m tools.check_integrity --base 81aead87d78ed468c4c374fb42921471b44c3f80
cd tasks/benchmark-design/suite
uv run --frozen python -m unittest discover -s tests -v              # candidate tests
uv run --frozen python -m runner --plan workload.json --mode full --dry-run
uv run --frozen python evaluate.py --events fixtures/public/stall --workload fixtures/workload.mock.json --out /tmp/r.json
cd .. && uv run --frozen python validation/build_validation.py      # rebuilds validation/ from fixtures
```

Seeds: `seeds.prompt_seed 20260101`, `arrival_seed 20260102`, `bootstrap_seed 20260103`
(plan); mock fixture seeds 100+ (public), 500+ (held-out), 900+ (mutations). Engine lock
digest (sha256 of `baseline/lock.json`) and plan digest are recorded in every `run.json`.
Enhanced Pi profile/release identity: **not released** (`docs/RELEASE_STATUS.md`); recorded
as `UNAVAILABLE_IN_DRAFT` in `submission.json`. Organizer harness, root tests, fixtures and
baseline are byte-identical to the starter commit (`validation/integrity.txt`).

## 7. Threats to validity

1. **One completed development run, no configuration speedup.** October 28
   calibration/quick/full evidence is linked above. The discrimination/control tables
   in section 4 remain mock results with injected delays. Real A/A remains inconclusive
   overall and establishes neither measured power nor population coverage.
2. **Tokenizer evidence** (§2): historical mock counts are regex pre-tokens. An optional
   pinned-model Tokenizers 0.22.2 adapter now re-encodes completed decoded output, with
   exact snapshot/runtime identities and conservative NFC-cluster timing. Real capture
   and evaluation require it; see `suite/README.md`. It does not recover generated token
   IDs, stripped special tokens, exact chat-template prompt counts or individual token
   arrivals. Prompt construction is still approximate and LLMD parity needs real validation.
3. **Calibration coverage is limited.** The frozen real fit covers approximate prompt
   lengths 44–4117 and the scoring plan's 44–4081 range. It is an observed upper
   residual envelope, not a prediction interval or proof of tokenization parity.
4. **Single client host and historical lag coverage**: network jitter is not separated
   from server behaviour. The original full run reports filtered maximum lag 8.51089 ms,
   but lacks raw lag intervals/global maxima, so omitted boundary-crossing stalls cannot
   be excluded. Recorder 1.1.1 fixes overlap accounting for future captures; later replay
   cannot recover this historical evidence. No perfect host isolation is claimed.
5. **Mock realism**: faults are stylised (fixed 1.5 s stalls, 8-token bursts, 3× usage).
   Real regressions are gentler; the decision rule's minimum effects and the sample sizes
   were chosen for plausibly detectable effects, not for the mock.
6. **Comparison power** depends on repetition count; three repetitions × 30+ requests was
   sized by budget, not by a measured variance — the A/A cells exist to expose that.
7. **Planning arithmetic** assumes no retries and no failed launches; a failed cold launch
   consumes ~9 planned minutes each.

## 8. Supplied material, candidate interfaces and remaining measurement limits

| Piece | Organizer-supplied | Candidate-defined implementation | Interpretation / remaining limit |
| --- | --- | --- | --- |
| `benchmarks/metrics.py`, `benchmarks/streaming.py` | yes (unmodified, imported) | — | — |
| `baseline/lock.json`, `budgets.toml`, `tools/check_integrity.py`, root tests | yes (read only) | — | `takehome-start` tag |
| Measurement recorder | no | `suite/runner/` 1.1.0; bounded streams, immutable output; `capture.py` lifecycle/export | Usable candidate recorder, not an organizer-trusted timestamp authority |
| Plan / raw-event schemas | no | `suite/workload*.json`, frozen event schema 1.0.0 in `suite/schema/` | Reproducible as supplied; future adapters are optional |
| Pinned engine on MI355X | pinned image/model supplied | Real calibration/quick/full on digest-bound LLMD wrapper; historical mock fixtures | A/A overall inconclusive; measured power not established; actual wrapper image digest disclosed separately |
| Fault / control fixtures | no | `suite/fixtures/{public,heldout}` plus independent timing checks | Development fixtures; opaque original key is not retained locally, so its keyed check skips explicitly |
| Engine tokenizer / per-token timing | no | pinned model assets + output re-encoding adapter (`delta`); regex for historical mocks/approximate prompts | LLMD parity, chat-template prompt adapter and true per-token arrival evidence |
| Enhanced Pi harness, profile/release digests, receipts | no | Candidate execution/calibration/audit/cleanup tooling; source and run receipts | No official enhanced profile, receipt or grading result is asserted |

### Optional future adapter compatibility

* `bench/records.py`: add an adapter that maps their files onto `RunRecord`/`RequestRecord`;
  fields they do not provide become `null` and the gates report them as missing evidence.
* `workload.json`: re-express the same cells in their plan schema (configuration, not code);
  the `schema_version` field is there for that migration.
* If their adapter supplies per-token timestamps, `client_tokens.resolution` becomes
  `token` and fluidity uses true token times; the usage band can be tightened.
* The mock and fixtures remain as regression tests for the evaluator.

## 9. Effort and resource use

* Active engineering time: one autonomous agent session; wall-clock effort was not instrumented and is not claimed.
* Real calibration shared one MI355X allocation with Task 2. The allocation was
  created 05:57:55 UTC and confirmed absent 09:51:27.526476 UTC on 26 October:
  3h53m32.526476s, rate-ceiling estimate **$21.41**, pending the provider invoice.
  This is shared allocation time/cost, not Task 1's exclusive execution time and not
  a successful full-suite runtime. Earlier failed allocations remain in the private
  resource ledger; they are not erased or charged again here.
* CPU use: fixture generation and validation on one VM (`validation/timing.md`).
* October 28 calibration, quick and full completed; capture/export completion was
  126.203 minutes from the original preparation start. The separate cleanup receipt
  confirms GPU absence after 139.470 minutes. The shared base-allocation upper estimate
  is $9.66, not Task 1 exclusive spend; the provider invoice remains pending.
* Cuts and limits: no configuration A/B speedup, measured power, model-judged quality,
  multi-turn or prefix-cache-hit cells. Historical mocks retain placeholder prefill
  deadlines; October 28 real scoring uses its own prior frozen fit. A brief CPU-only
  metadata build overlapped full capture and is disclosed in provenance.

## 10. Issues found in supplied infrastructure

None that block the task. Observations, not fixes (protected files untouched):
`benchmarks.metrics.minimum_decode_deadline` returns `None` for all-at-once delivery — the
evaluator propagates this as an unresolved trace and withholds FTGR rather than reporting
infinite speed, as the docstring requests. `submission.example.json` points the integrity
command at the absent `takehome-start` tag; the submission records the commit SHA instead.
