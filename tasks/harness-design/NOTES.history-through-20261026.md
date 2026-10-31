# Task 2 — Candidate notes (harness-design)

**Latest real result (26 October):** the pinned LLMD/Qwen3.6-27B baseline on one MI355X
passed 30/30 measured requests. A retained execution receipt reports a later successful
30-request exact revalidation, but its full report and fresh response records were not
preserved offhost before teardown; only the PASS receipt and original report hash remain.
The original full baseline report is retained. The exact
5,923,200,000-byte baseline archive is [publicly downloadable](https://github.com/sharonbasovich/performance_takehome/releases/tag/task2-llmd-baseline-20261026);
Codex independently downloaded every public part and verified the ordered full SHA-256
`c759f7c2a7dd3b6a1deb083c0df46c435c0165e237f50d6b7e52097031497b47`.
See `harness/reports/live-mi355x-20261026/` and the current `submission.json`.

The genuine GLM-5.2/Pi attempt failed at a buffered upstream 120-second response
deadline after 13 read-only tool calls, before any patch. All 146 workspace files
matched the baseline. No successful propose/build/measure/keep-or-revert experiment,
optimized image or speedup is claimed. The unresolved final API request retains its
full charge reservation. The baseline was later published and anonymously verified through the separate
[delivery PR 5](https://github.com/sharonbasovich/performance_takehome/pull/5):
`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:7db01160c57479b9d608b7c2e6fc4e6b5d438e53eb4d44dc9f3ab0441d1bb20d`.
The [full anonymous object check](https://github.com/sharonbasovich/performance_takehome/actions/runs/40598808475)
verified all 25 objects (5,923,167,710 bytes); a separate anonymous Docker pull,
identity inspection and CPU help check also passed. These support baseline
delivery only. No optimized image or GPU inference result follows from them. Delegated CPU validation in
[draft PR 5](https://github.com/sharonbasovich/performance_takehome/pull/5), reviewed by
the root agent, confirms `docker load`, preservation of the image index and an isolated
`--help` run with network disabled. The root agent did not execute that import test.
PR 5 contains the exact-byte preservation, restore instructions and import evidence.
The historical CPU stand-in manifest is preserved as `submission.cpu-standin-legacy.json`.

The current live-manifest compatibility revision also passed **241/241** native tests,
zero skips, locked Ruff and protected integrity. Historical CPU provenance checks use
the exact archived legacy manifest; a separate check binds this live manifest to the
unchanged baseline report, original TSV source and image identity. See
`harness/reports/live-mi355x-20261026/manifest-compatibility/`.

**Read this first.** The performance tables below are historical **CPU stand-in development
evidence** produced on an 8-vCPU x86 VM against a **candidate-built llama.cpp server**.
They are not corrected GPU performance or Pi optimization results. Live LLMD request
compatibility checks are described separately below; no official grade is claimed.

Starter commit: `81aead87d78ed468c4c374fb42921471b44c3f80` (the organizer's `takehome-start`
tag does not exist in this draft; the only upstream commit SHA is used instead and was **not**
tagged by me). Integrity output is in the "Integrity" section below.

## Optional prebuild delivery eligibility (28 October)

`--delivery-policy llmd-config-v1` rejects an actual applied candidate before
build or measurement when it changes files outside the selected Dockerfile,
uses filesystem instructions or parser directives, or changes more than one
semantic tuning knob. Separately kept single-knob changes can accumulate; the
reviewed flag family includes the built-in native drafter. Generic mode defaults
to `none`. Select the policy for both the fresh baseline and genuine Pi run;
resume must preserve the original mode. See [DELIVERY-POLICY.md](harness/DELIVERY-POLICY.md).

The [CPU verification receipt](harness/reports/delivery-policy-cpu-20261028.json)
records 273 tests: 272 pass and one actual-image check is skipped because the
pinned image is absent. The policy has 22 new cases; independent review also
checked 60 directive/comment boundaries. Protected integrity and whole-Harness
Ruff pass. A formatter-only loop change has an identical complete AST to the
tested source. Initial verification environment failures and the unchanged
protected-root lint finding remain disclosed in the receipt. This optional
source policy does not establish image layers, runtime flag support, a successful
Pi cycle or a performance gain. Final publication must still check actual config
and the complete immutable graph.

## LLMD request compatibility profile

Use `--development-benchmark tasks/harness-design/harness/development-benchmark.llmd.json`
for the pinned LLMD target. The named `llmd_closed_thinking_v1` profile appends the fixed
assistant prefix `</think>\n\n` and fixes continuation flags for both startup and measured
requests. It contains no answer text. The default benchmark remains available for other
engines; conflicting request overrides are rejected.

On the actual MI355X/pinned target, this profile produced completed final answers without
reasoning output, including a short integer, a planted long-context fact and a complete
long integer sequence. These are compatibility observations, not performance comparisons.
Ordinary short requests had exhausted their token caps in reasoning, and accepting a
thinking-control field did not demonstrate that the server honored it. Establish fresh
baseline and candidate measurements using the identical frozen profile and retain the
complete benchmark JSON and its hash with the run evidence.

## Final review correction: historical FTGR is invalid

The review of `4887cd9` found that the measurement adapter passed cumulative
token arrival times to Etalon's interval-based FTGR function. This can keep a
slower decoder merely because its first token arrived earlier. The adapter now
converts arrivals with the protected `metrics.token_intervals()` function.
Regression tests reproduce the incorrect keep and require rejection after the fix.

The original 14 rows, raw reports and patch chain below remain immutable audit
history. Their FTGR values, FTGR speedups and resulting keep decisions are invalid
as performance evidence; the historical narrative and tables describe what the
old implementation recorded, not a validated improvement. No corrected replay or
GPU result is claimed. Structural `validate-results` success cannot certify metrics.
Any new performance claim requires fresh measurements with the corrected adapter.

The same review found two restart gaps. Paid proposers now persist an unresolved
call marker before launch, then durably record a settled cost before artifact work;
an interruption or unknown charge blocks further calls until billing reconciliation.
An interrupted baseline and its successful retry use distinct row/report/build-log
identities. Results validation admits explicitly failed baseline attempts before the
single successful baseline while still rejecting candidate attempts before it.
The opening README invocation now explicitly selects Pi and its credential variable.

Independent follow-up reproductions also covered the supported corrupt-main backup
fallback and scripted queue recovery after a failed baseline. Both main and backup
checkpoints now contain the pending-call marker before launch; baseline retries consume
evidence IDs without consuming unstarted scripted experiments. Two additional regression
tests exercise the actual fallback and a fresh queue restart.

A separate tiny Docker 29/containerd round-trip found that `.Id` identifies an
index/manifest on that store, rather than raw config bytes. Newly generated manifests
therefore use the conservative `docker-resolved-image-digest` label. Raw config,
platform manifest, index and publication linkage still require explicit verification;
historical manifest bytes and their original labels remain unchanged.

## Approach in one paragraph

`harness/run.py` drives an unattended loop (`abstract_harness/loop.py`): build the baseline
image -> measure it and record golden outputs -> for each attempt: budget guard, ask the
proposer for exactly one patch, copy the **last-good build context**, apply the patch, build
a fresh image (digest recorded), start it on an internal no-egress Docker network with the
model bind-mounted read-only, wait for readiness + first valid generation, run the correctness
gates, **re-measure the last-good image in the same attempt (A/B)**, measure the candidate,
decide with a prompt-stratified bootstrap, keep (retag `last-good`, promote the context) or
revert, and append one row to `results.tsv` plus a JSON development report. State is
checkpointed to `state.json` after every step so a restart resumes; SIGTERM/SIGINT finalize
and exit 3. The `llmd` engine uses the pinned image and the exact device/mount/security flags
of `tools.baseline.start_command`; the `standin` engine is the CPU stand-in used for evidence.

## Organizer-supplied vs candidate-built vs still missing

| Piece | Organizer supplied | Candidate built (stand-in) | Still missing |
| --- | --- | --- | --- |
| Serving engine | pinned LLMD image (`baseline/lock.json`), closed source | `standin/` llama.cpp `llama-server` (commit `620d2225`, tarball sha256-pinned) + SmolLM2-135M-Instruct Q8_0 GGUF (sha256-pinned) | LLMD source, MI355X/ROCm host |
| Candidate image | `baseline/Dockerfile` (reference) | `tasks/harness-design/Dockerfile` wrapper (builds on CPU; `/llmd/llmd` entrypoint + `--io-impl=zio` preserved) | a GPU run of that image |
| Pi launcher | `agent-harness/profiles.json` pins package/version/integrity/flags; no launcher | `PiProposer` (print mode, stock tools, isolation flags, task-scoped `PI_CODING_AGENT_DIR`, key by env-var name) | coding-model API key; organizer launcher |
| Proposer used for evidence | - | `ScriptedProposer` + `experiments/standin-queue.json` | - |
| Benchmark | `benchmarks/metrics.py`, `benchmarks/streaming.py` (used unmodified) | `development-benchmark.json` workload, gates, repetitions, decision rule | private official workload |
| Runner / grader | docs only | `run.py` manifest + integrity report | official grader output, receipts, profile digests |
| Budget data | `budgets.toml`, `tools/budget.py` | pre-attempt guard (`budget.py`) | reconciled billing (API spend is 0 here) |

## Reality constraints and how they were handled

* **No MI355X / ROCm**: the `llmd` engine path is implemented and unit-tested (flags, mounts,
  entrypoint, `--network none` probe) but **never executed with a model**. `docker build
  --platform linux/amd64` of the wrapper succeeded on CPU; `docker run --rm --network none
  --entrypoint /llmd/llmd <pinned> --help` exits 0 on CPU (help text is written to stderr);
  an unknown flag fails with an unexpected-argument error; `--io-impl=zio` is accepted though
  undocumented; the pinned image has **no `/bin/sh`**, so the wrapper Dockerfile has no `RUN`.
* **No coding-model key**: Pi 1.0.0 was installed from npm with the integrity hash from
  `profiles.json` (Node 22.23.4, `pi --version` prints `1.0.0`); `PiProposer` is complete up to
  the model call and refuses to start (and makes no subprocess/network call) when the key env
  var is absent. All `results.tsv` rows come from `ScriptedProposer`.
* **No organizer Pi launcher / schema / fixtures**: candidate stand-ins above; interfaces take
  config (`--engine`, `--proposer`, benchmark JSON with `schema_version`) so real pieces drop in.

## Measurement (development benchmark)

`development-benchmark.json`: 6 greedy prompts (temperature 0, top-k 1), 5 repetitions each,
`max_tokens` per prompt, streamed `/v1/chat/completions` with `stream_options.include_usage`.
Per request: TTFT = client request start -> first content delta; FTGR = protected
`benchmarks.metrics.fluid_token_generation_rate` over the per-token arrival times from
`benchmarks.streaming`; startup = container start -> readiness -> first valid complete
generation. Gates (any failure => `gate_failed`, never kept): finish reason `stop`/`length`,
non-empty content, streamed token count within tolerance of `usage.completion_tokens`,
greedy self-consistency (two identical requests give identical text), and byte-equality with
the baseline's golden outputs for every prompt.

Decision rule (`decision.py`): per prompt, median speedup candidate vs the **same-attempt**
last-good measurement; geometric mean across prompts; 90% percentile bootstrap resampled within
each prompt group (deterministic seed). `improved` requires both TTFT and FTGR not regressed,
at least one significantly improved (CI excludes 1 and effect >= 3%) and startup not
significantly worse; `regressed` if any primary metric's CI is entirely below 1; otherwise
`no_change`. Both primary metrics must have finite positive samples for identical
prompt groups, matching repetition counts, and at least `--min-samples` observations
per prompt per side. Incomplete or invalid primary evidence => `no_change`, even if
the other metric improves. A missing last-good image or a current control that fails
correctness makes the attempt an `error`; historical baseline samples never substitute
for that control. These stricter promotion checks were added after the historical CPU
rows below and do not relabel those measurements as new evidence.

Why A/B inside the attempt: the first evidence runs compared each candidate with a baseline
measured many minutes earlier; background load on the shared VM drifted between the two and
produced a spurious `improved` for a no-op change. Re-measuring last-good right before the
candidate removed that bias (cost: 2x measurement time per attempt).

## Evidence: `harness/results.tsv` (stand-in CPU rows)

| experiment | kept | outcome | TTFT x | FTGR x | startup s | build s | harness note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 1 | baseline | - | - | 0.736 | 32.82 | ttft_median_s=0.0403 ftgr_median_tok_s=8.2403 reps=5 |
| 001-threads-8 | 1 | improved | 1.4206 | 1.4465 | 0.747 | 104.49 | significant improvement: ttft_s x1.421, ftgr_tok_s x1.447 |
| 002-threads-1 | 0 | regressed | 0.2126 | 0.2567 | 0.846 | 110.96 | primary metric regressed: ttft_s x0.213, ftgr_tok_s x0.257 |
| 003-ubatch-64 | 0 | no_change | 0.9329 | 0.9295 | 0.718 | 108.84 | no primary metric moved significantly |
| 004-repeat-penalty-1.5 | 0 | gate_failed | 0.9794 | 0.6219 | 0.739 | 105.79 | golden_output_match: 6 prompts differ from the baseline golden outputs (showing  |
| 005-port-8001-misconfig | 0 | error | - | - | - | 105.94 | MeasurementError: readiness timeout after 90s: ConnectionRefusedError: [Errno 11 |
| 006-avx512-on-host-without-avx512 | 0 | error | - | - | - | 105.1 | MeasurementError: engine process exited before serving /v1/models (ConnectionRef |
| 007-native-march | 0 | interrupted | - | - | - | 16.61 | interrupted by SIGTERM (RuntimeError: build failed: build failed (rc=130): #9 10 |
| 008-lto-on | 0 | no_change | 0.9767 | 0.9861 | 0.780 | 101.94 | no primary metric moved significantly |
| 009-flash-attn-on | 0 | no_change | 0.9946 | 1.0115 | 0.727 | 104.19 | no primary metric moved significantly |
| 010-parallel-4-slots | 0 | no_change | 1.0464 | 1.0764 | 0.727 | 103.5 | no primary metric moved significantly |
| 011-no-warmup | 0 | no_change | 1.0128 | 1.0067 | 0.710 | 103.07 | no primary metric moved significantly |
| 012-kv-cache-q8 | 0 | gate_failed | 0.9547 | 0.9810 | 0.718 | 106.4 | golden_output_match: 3 prompts differ from the baseline golden outputs: ['colors |
| 013-openmp-off-cold-rebuild | 1 | improved | 1.0713 | 1.1145 | 0.714 | 101.29 | significant improvement: ftgr_tok_s x1.114 |

Speedups are candidate vs the same-attempt re-measurement of the last-good image (>1 = faster). All 14 rows were produced by **one** queue run from the committed harness tree (`source_commit` = `45fc163eba7c`, the commit whose code wrote them): one `run.py run ... --no-cache` invocation, a real SIGTERM injected while `007-native-march` was building (row `interrupted`, exit 3), and a second invocation that resumed from `state.json` and finished the queue. Every build in this set is an uncached llama.cpp compile (`build s` column, `build.no_cache: true` in each report). `012-kv-cache-q8` is rejected by the golden-output gate; `013-openmp-off-cold-rebuild` was kept (x1.07 TTFT / x1.11 FTGR, CI excludes 1). `error` rows have blank metrics (blank != 0) and GPU hours are blank because `--gpus 0`.

Every row was written by `run.py`; nothing was hand-edited.  Per-attempt JSON reports with raw
samples, container commands and log tails are in `harness/reports/`; patches in
`harness/patches/`. `error` rows come from fault injection (a wrong `--port` so readiness
never arrives; `GGML_AVX512=ON` on a host without AVX-512 so the binary dies at startup),
`gate_failed` from `--repeat-penalty 1.5` (changes greedy output -> golden mismatch), and
`interrupted` from a real SIGTERM sent while an experiment was in its `building` stage; the
next invocation resumed from `state.json` and continued the queue.

## Failures and bugs found on the way (all fixed unless noted)

1. Wrapper Dockerfile with `RUN printf` failed: pinned LLMD image has no `/bin/sh`.
2. Pooled bootstrap over heterogeneous prompts gave CIs too wide to ever decide -> stratified.
3. Queue edits that assumed a previous candidate was kept failed to apply -> regex edits.
4. `--n-predict 8` did not trip the golden gate because the client sends `max_tokens` ->
   replaced by `--repeat-penalty 1.5`.
5. Baseline drift under host load (see A/B above).
6. Candidates were built from the pristine context instead of the last-good context, so after
   the first keep every candidate silently lacked the kept change and "regressed" by the same
   ratio -> persistent `last-good-context` promoted on every keep. Runs 1-4 were discarded.
7. Publishing a host port on an `--internal` network does not work -> container IP is used.
8. Pi cost accounting over-counted: JSON mode attaches the running `usage` to every
   `message_update` delta (`dist/modes/json-event.js`), so summing every `cost.total` in the
   transcript multiplied the spend. Fixed to count only `message_end` events; found while
   building the Pi test double (below).
9. Adversarial pass (reviewer-requested) found and fixed: a corrupt/truncated `state.json`
   crashed on resume (now falls back to the `.bak` of the previous checkpoint, or refuses to
   start fresh with a clear error instead of duplicating the baseline row); a torn partial
   last line in `results.tsv` would have been glued to the next row (now cut off into
   `results.tsv.torn` and rejected by `validate-results`); two harness instances could share a
   state directory (now an `flock` on `harness.lock`, exit 4); a backwards wall-clock step
   could extend the lease (budgets now use a clamped, persisted timestamp); a model error
   inside Pi (`stopReason: error`) was indistinguishable from a crash (now surfaced with its
   `errorMessage` and the spend so far). Container-exits-before-readiness and ready-but-every-
   request-500s paths were already errors; the latter now has a test against a stub server.
10. First regenerated evidence set (discarded) showed the last-good A/B arm 2-3x slower in some
    attempts than in others (threads-8 FTGR median 13.9 vs 4.4 vs 7.4): BuildKit's post-build
    export/GC after each `--no-cache` compile was still running when the first measurement arm
    started. Fixed by `procs.wait_for_quiet_host()` (bounded wait for >= 85% idle over 3 samples,
    recorded as `host_quiet_before_start` in every container record; when `/proc/stat` cannot
    be read it records `quiet: null` plus a `reason`, never `true`). Re-derived from the
    committed reports (`baseline_measurement.ftgr_samples` medians, tok/s): the last-good arm
    measured 9.64 in attempt 001 (the thread-4 baseline image, vs 8.24 in the baseline row
    itself), then 12.01-15.43 across attempts 002-013 (12.69-15.43 if the `error` row 006, whose
    candidate never served, is excluded). That is still +-13% drift on a shared host, which is
    why only the paired A/B ratio inside one attempt is used for decisions.

## Pi test double (what `PiProposer` was actually tested against)

No model was ever called. `harness/tests/fixtures/fake_pi.py` is a CANDIDATE-BUILT executable
that speaks the pinned CLI's non-interactive protocol, derived by reading the installed
`@earendil-works/pi-coding-agent@1.0.0` package:

| Behaviour | Source in the installed package |
| --- | --- |
| `--print` / `-p` flag, `@file` prompt arguments, `--mode` text/json/rpc | `dist/cli/args.js` L134-135, L224-225, L47 |
| First stdout line is the `SessionHeader` `{type:"session", version, id, timestamp, cwd}` | `dist/modes/print-mode.js` L97-101; `dist/core/session-manager.d.ts` L5-12 |
| One JSON line per session event | `dist/modes/print-mode.js` L85-87 (`JSON.stringify(toJsonEvent(event))`) |
| `message_update` rewritten to `{type, usage, assistantMessageEvent}` | `dist/modes/json-event.js` (`toJsonEvent`) |
| Event types `agent_start`, `turn_start`, `message_start`, `message_update`, `message_end`, `tool_execution_start/update/end`, `turn_end`, `agent_end` | `node_modules/@earendil-works/pi-agent-core/dist/types.d.ts` L451-483 |
| `usage` = `{input, output, cacheRead, cacheWrite, totalTokens, cost:{input, output, cacheRead, cacheWrite, total}}` | `node_modules/@earendil-works/pi-ai/dist/types.d.ts` L292-310 |
| Assistant `stopReason` (`stop`/`length`/`toolUse`/`error`/`aborted`) and `errorMessage` | `node_modules/@earendil-works/pi-ai/dist/types.d.ts` L386-388 |

`FakePiEndToEndTest` drives the real `PiProposer` against it: a successful run yields exactly one
`patch -p1`-applicable diff and the spend from `message_end` events only (0.0048 + 0.0075);
non-JSON lines in the stream are skipped without losing cost; a model error (`402
insufficient_quota`) is surfaced with its message and spend; a non-zero exit, a hang (killed
at the timeout) and a run that edits nothing are all `ProposerError`s. Not verified: that a
real provider's error text and exit code match the fixture's; the fixture's values are
illustrative, its *shapes* are the ones in the type definitions above.

## Round-3 review fixes (independent cold review of 62ab024)

Every finding below was reproduced, fixed and covered by a test in `harness/tests/`
(`test_hardening.py`, `test_docs.py`, `test_provenance.py`, `CrashBetweenWritesTest`):

| Finding | Fix | Test |
| --- | --- | --- |
| Scripted edit with an absolute `file` path wrote to a host file | `proposers.contained_path()` (relative, no `..`, resolves inside the context, no symlink components); contexts with escaping symlinks are refused before copying; `apply_patch` rejects symlinks it created that leave the context; copies preserve symlinks instead of following them | `PathContainmentTest` (absolute, `..` variants, symlink file, symlinked dir, case variant, patch-created symlink) |
| Startup regression guard was dead code (1 sample vs `min_samples=3`) | `decision.startup_hard_gate()`: single-sample hard gate at 1.5x (`--startup-regression-limit`), evaluated before the primary metrics; docs now say exactly this | `StartupHardGateTest` (100x slower startup + TTFT win -> `regressed`) |
| `kill -9` between row append and state save duplicated the row and overwrote the report | `InProgress` carries `attempt_index`/`report_path`/`row_written`; `_append_row` is idempotent on experiment id; recovery clears the marker without a row when the row exists, never overwrites an existing report (`.recovered.json`), and `_reconcile_attempt_index()` never reuses a number already in `results.tsv` (also covers a `.bak` rewind); `validate-results` rejects duplicate ids | `CrashBetweenWritesTest` (report-only, row-without-save, `.bak` rewind, duplicate id) |
| Documented `fetch-standin-model --model-dir` did not run | README fixed (`--dest`); organizer example rewritten to the real CLI | `test_docs.py` parses every documented `run.py` line with the real parser |
| `source_commit` named a commit without the code | `_git_describe()` records HEAD and refuses to run on a dirty `tasks/harness-design` unless `--allow-dirty` (then `-dirty` suffix); only the files this run is configured to write (`--results`, `--reports-dir`, `--patches-dir`, `--out`) are exempt from the dirty check (Round 4; before that `reports/` and `patches/` were always exempt); `harness_tree_sha256` of the code + inputs stored in `state.json`, `submission.json` (`source.harness_tree_sha256`) and `reports/build-provenance.json` (Round 4; before that state only); **all evidence rows were regenerated from the committed tree** | `ProvenanceTest` |
| Budget guard could not bound an attempt | estimate = max(configured, p90 of observed attempt durations); proposer USD estimate passed; every stage timeout is clamped to a per-attempt deadline = min(worst case from timeouts, usable lease window) | `BudgetBoundTest` |
| `results.validate()` gaps | rejects `improved` rows without a speedup > 1 (none below 0.95), NaN/inf/non-numeric numbers (named error), non-hex digests, malformed timestamps; file-level: duplicate ids, duplicate rows, non-monotonic timestamps | `ResultsValidationTest` |
| `load_benchmark()` gaps | `BenchmarkError` for non-positive/non-int repetitions and token counts, non-positive timeouts, prompts without `id`/`prompt`, bad `startup_probe` | `BenchmarkValidationTest` |
| Complete last row without newline was dropped to `.torn` | fragment is parsed first; a valid row is kept (newline added, logged) | `test_complete_last_row_without_newline_is_kept` |
| Lock taken after header write/state read; results path unlocked | both `harness.lock` and `<results>.lock` are taken in `Harness.__init__` before any read/write | `EarlyLockTest` |
| `KeyError`/`TypeError` escaped `run()` without `finalize()` | queue entries are schema-checked up front (`ScriptedProposer.validate_queue`); `run()` catches `Exception`, logs the traceback, finalizes, exits 2 | `test_queue_schema_errors_are_named` |
| Signal handler raised inside the handler and `SIG_IGN`ed a second signal | first signal sets a flag (child processes are aborted via `should_abort`, the loop stops at the next checkpoint and finalizes); a second signal aborts even `finalize()`; a third is the default action | `SigtermSubprocessTest` |
| Hard-coded manifest literals; empty `agent_transcripts` | repository URL from `git remote`, Pi pins from `agent-harness/profiles.json`, llama.cpp commit from the stand-in Dockerfile; transcripts collected from reports | reconciled by `validate-results` + manifest tests |
| FTGR silently dropped <= 1-token samples; `gpu_hours_cumulative=0.0000` with no GPU | dropped count reported in measurement notes; GPU hours blank when `--gpus 0` | `test_measure.py`, evidence rows |
| Tautological commit check in `standin/Dockerfile` | `ARG LLAMA_CPP_COMMIT` is now part of the `ADD --checksum` URL, so commit and checksum must change together | build of the evidence images |

**What the "digest" is.** With `docker buildx build --load` the only locally resolvable identity
is the image *config ID* (`docker image inspect --format {{.Id}}`); that is what `image_digest`
holds and why it equals `image_id` in every report. A registry manifest digest exists only after
a push. `submission.json` (`source.image_identity_kind`) and `reports/build-provenance.json`
(`image_identity_kind`) record the kind explicitly: `docker-image-config-id`.

**Pi sandboxing (known limitation).** The organizer contract requires plain Pi 1.0.0 with the
stock `bash` tool, so `PiProposer` runs it as a host process. Mitigations in place: cwd is the
scratch copy of the build context, the environment is scrubbed to PATH/HOME/LANG plus the named
key, `PI_CODING_AGENT_DIR` points at a task-scoped directory, the resulting tree is diffed and the
patch is containment-checked before use. Not in place: a container or `bwrap` jail; on a shared
host that is a real risk and is called out in the PR. `--no-approve` in 1.0.0 means "ignore
project-local files" (`dist/cli/args.js` L219, `projectTrustOverride`), not approval skipping.

## Round-4 review fixes (independent re-review of 430a443)

The reviewer confirmed the six Round-3 majors and found one new major, two regressions
introduced by Round 3, and documentation drift. All fixed at this head; the evidence rows were
not touched (their `source_commit` 45fc163 is still the code that produced them); only the
derived manifest files were regenerated with `--manifest-only`.

| Finding | Fix | Test / artifact |
| --- | --- | --- |
| The documented reproduction command overwrote `reports/000-baseline.json` and `build-provenance.json` in the checkout, and the dirty check ignored `reports/` | `--results`/`--reports-dir`/`--patches-dir` default to files under `--state-dir`; `run` refuses (exit 2, before touching disk) when any output path is a git-tracked file unless `--overwrite-evidence`; the dirty check exempts only the paths this run is configured to write; README/PR commands write under `/tmp/harness-state`; the README command was run verbatim in a clean clone and `git status --short` stayed empty | `test_tracked_evidence_is_detected_and_defaults_avoid_it`, `test_run_refuses_to_overwrite_committed_evidence`, `test_dirty_detection_ignores_evidence_outputs_but_not_code` |
| `_release_locks()` unlinked `<results>.lock` (unlink race: two holders) | Lock files are never unlinked; the results lock now lives outside the repo (`procs.results_lock_path()`: `$TMPDIR/abstract-harness-locks/<sha256(path)>.lock`) so it cannot litter the checkout either | `test_lock_files_survive_release_and_block_another_process` (second process holds the flock -> `LockError`; file survives release) |
| `manifest._pi_pins` read `integrity` but `profiles.json` uses `artifact_integrity` (empty `pi_integrity`) | Reads `artifact_integrity` + `node_requirement`; raises when any pin is empty; `agent-provenance.json` regenerated | `test_pi_pins_come_from_profiles_json_and_are_nonempty`, `test_committed_agent_provenance_carries_the_pi_pins` |
| `harness_tree_sha256` only in `state.json` | In `submission.json` `source` and `reports/build-provenance.json`; the test re-derives it from `git archive <source.commit>` | `test_committed_manifest_tree_hash_matches_its_source_commit`, `test_manifest_records_tree_hash_and_image_identity_kind` |
| "manifest says which digest kind" was not true | Explicit `image_identity_kind: "docker-image-config-id"` in both files | same tests |
| Quiet gate recorded `quiet: true` when `/proc/stat` was unreadable | `quiet: null` + `reason`; the loop logs "host idle unknown" | `test_unavailable_sampler_does_not_block` |
| NOTES "12.7-13.7 tok/s" | Re-derived above from the reports (9.64 / 12.01-15.43) | `harness/reports/*.json` |
| PR body outcome counts stale (013 is `improved`) | Recounted from `results.tsv`: baseline 1, improved 2 (001 threads-8, 013 OpenMP-off; both kept), regressed 1, no_change 5, gate_failed 2, error 2, interrupted 1 | `validate-results` output in the PR |
| Minor: startup hard gate silently passed a candidate with no finite startup sample | Candidate without a finite startup sample fails the gate | `test_missing_or_nan_candidate_startup_fails_the_gate` |
| Minor: patches could create symlinks inside the context | Any symlink created by a patch is rejected | `test_apply_patch_rejects_symlink_creation_inside_context` |
| Nit: CRLF `results.tsv` accepted | Rejected (`results.tsv is LF-only`) | `test_crlf_results_file_is_rejected` |

## Limitations (honest)

* Stand-in results say nothing about LLMD on MI355X; they prove the loop, not the hypotheses.
* 8 shared vCPUs: absolute numbers are noisy; only the paired A/B ratios are meaningful.
* Scripted proposer only for the evidence rows; the Pi path is exercised end to end against the
  test double above but never against a real model/provider.
* `extra_request_fields.cache_prompt=false` in the benchmark is llama.cpp-specific; set it to
  `{}` for LLMD if it rejects unknown fields (documented in the JSON).
* GPU-hours are allocation x elapsed; API spend is only known when the proposer reports cost.
* One `interrupted` experiment (`007`) was not retried; the queue continued past it by design.

## Independent local audit after the recorded stand-in run

The recorded evidence above is preserved. A later CPU-only review found and fixed three
execution issues that were not exercised by those evidence rows:

* Resume now preserves the original allocation start and deadline. Previously, restarting the
  CLI reset GPU-hour accounting and moved the usable lease window forward.
* The LLMD build now preserves `tasks/harness-design/Dockerfile` relative to its repository-root
  build context, including scratch copies. Previously it looked for a nonexistent root Dockerfile.
* The baseline now checks its allocation window before building; the proposer receives the
  remaining deadline and signal-abort callback; blocking HTTP startup and benchmark stages run
  in child processes that the parent can terminate at the stage/allocation deadline. The GPU-hour
  remainder also caps that deadline. Process/container cleanup may use its bounded grace period.

The real-target runbook below writes fresh evidence outside the checkout. These fixes are covered
by offline regression tests, including a stalled HTTP test server and the fake Pi executable.
They do not constitute a new GPU run, image build, or performance result. The old reports and
manifest still identify the source that produced the old evidence; regenerate them after running
the corrected harness on the actual target.

## Runbook when an MI355X host and a coding-model key exist

```sh
git clone <fork> && cd performance_takehome && git checkout submission/harness-design
uv sync --frozen --extra dev
docker pull --platform linux/amd64 docker.io/zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f
# 1. contract probe (no GPU needed)
docker build --platform linux/amd64 -t llmd-candidate:probe tasks/harness-design
docker run --rm --network none --entrypoint /llmd/llmd llmd-candidate:probe --help
# 2. Pi distribution (exact pin from agent-harness/profiles.json)
mkdir -p /opt/pi && cd /opt/pi && npm init -y >/dev/null && npm install @earendil-works/pi-coding-agent@1.0.0 && ./node_modules/.bin/pi --version && cd -
# 3. run (the key is read from the named env var only)
export ANTHROPIC_API_KEY=...
uv run --frozen python tasks/harness-design/harness/run.py run --engine llmd \
  --model-dir /path/to/model-repo --render-device /dev/dri/renderD128 \
  --proposer pi --pi-bin /opt/pi/node_modules/.bin/pi \
  --pi-provider anthropic --api-key-env ANTHROPIC_API_KEY \
  --state-dir /var/tmp/harness-state --out /var/tmp/harness-state/submission.json \
  --budget-minutes 1380 --api-spend-limit-usd 40 --gpus 1 --gpu-hours 24
# 4. verify
uv run --frozen python tasks/harness-design/harness/run.py validate-results --results /var/tmp/harness-state/results.tsv
python -B -m tools.check_integrity --base 81aead87d78ed468c4c374fb42921471b44c3f80
```
First experiments to run on the real target are ranked in `harness/program.md` (startup:
`--cache-memory-fraction`, telemetry-with-no-egress check; TTFT: `--prefill-chunk-size`,
`--token-batch-size`; smoothness: `--page-chunk-size`, `--batch-size`; speculative decoding
only with the same model/tokenizer and greedy-equal outputs).

## Integrity

```
$ python -B -m tools.check_integrity --base 81aead87d78ed468c4c374fb42921471b44c3f80
PASS: protected organizer files unchanged from 81aead87d78ed468c4c374fb42921471b44c3f80
```
The organizer `takehome-start` tag is absent in this draft fork, so the single upstream commit SHA is passed with `--base`; no tag was created.

## Effort and resource use

Active agent time: one unattended session (~a working day equivalent). API charges: 0 (no
coding-model calls). GPU-hours: 0 (none allocated). CPU: 8 vCPU VM; the committed evidence run
(14 rows, one queue, SIGTERM + resume) spanned 2026-10-24T09:42:50Z..10:08:59Z (26 min 9 s). Every build
was an uncached llama.cpp compile (`--no-cache`): 101-111 s per attempt image, 32.8 s for the baseline
image, 16.6 s into the `007` build when SIGTERM arrived. Per attempt ~2.5 min including the quiet-host
wait (3 s each arm), A/B re-measure and candidate measurement; 95 s extra for the readiness-timeout fault. Cut: Pi end-to-end run (no key),
LLMD execution (no GPU), more repetitions (time).

## 2026-10-25 metadata correction and implementation verification

The committed `submission.json` now lists the complete ordered kept-patch chain:
`001-threads-8.patch`, then `013-openmp-off-cold-rebuild.patch`. This is derived from
the two non-baseline kept rows in the historical 14-row `results.tsv`; rejected,
failed and interrupted patches are excluded. Applying that chain to the recorded
stand-in source retains both the eight-thread runtime settings and OpenMP-off build.

This corrects reproduction metadata only. The historical source commit
`45fc163eba7cdc34f27d377fbd4387ffe17b3627`, harness input hash
`f4a25e8e51d8b619672e5dcae9b0debe1e029518fad32d382f15de2741d30e81`, image config
ID, raw rows, measurement reports and timing/resource figures above are unchanged.
No experiment was rerun for this correction.

The later harness implementation at `c9ccf31281cb4bcd80239c73e0fcf5fb51a1ef2b`
passed 207/207 tests in a fresh LF Linux clone (Python 3.14.4; zero skips), locked
Ruff 0.16.10 and protected-file integrity checks. Those checks cover promotion
transactions, resumed queues, Docker deadlines/isolation, clock accounting,
complete measurement populations, patch-chain metadata, nested cgroup limits and
recursive state-directory placement (rejected before disk work or builds).
The actual pinned Pi 1.0 CLI also passed scripted loopback tool/usage tests; no model
inference or provider charge occurred. These implementation checks are separate
from the historical CPU measurements and establish no MI355X/LLMD performance result.

## 2026-10-25 second Greptile review corrections

Source `c61748a51ba8b7f8d3c1d4ce4418b7ed1a6919eb` addresses the two P1 findings
from the review of `95f6d3e`: rejected Pi output retains its reconciled API charge,
and recovery removes the exact saved server from a pre-namespace checkpoint.
Binary fallback diffs, unreadable patches and non-object metadata carry the paid
cost into the ledger before another admission check. Container absence requires
a successful full Docker name enumeration; a daemon error, timeout or surviving
server blocks recovery while preserving the checkpoint. Cleanup never sweeps
the old shared namespace.

A fresh LF Linux `95f6d3e` clone with the six-file candidate overlay passed
**218/218 tests, zero skips, in 30.275 seconds** on Python 3.14.4; the focused set
passed 32/32. After committing, its entire native Git tree was verified equal to
`c61748a` (`cbb1a28c881ff1a32fb23265a516e55b12e6cb3f`). Ruff 0.16.10 and protected
integrity against `81aead87d78ed468c4c374fb42921471b44c3f80` passed. Re-review of
the new head remains pending.

Actual pinned Pi 1.0/Node 22.19 also passed the separated-credential gateway fixture
against these tested bytes. Scripted loopback responses exercised stock tools and
terminal usage, including rejection before another request after missing usage;
actual provider spend for this fixture was US$0.

These code checks preserve the historical 14 measurements, source/input identities,
image config IDs and kept-patch chain 001 → 013 described above. They add no target
performance measurements or published registry manifest digest. The earlier
207-test packet remains a frozen historical artifact.

## Remote regression merge and committed-source verification

Merge `749438ffec910b07129feb545fae0b592bfd0918` retains remote commit
`d508ea05cdd617087d8a62217b4a9afcc22404e6`, including its fake-Pi binary mode
and real-subprocess regression test. The broader output-recovery cost handling
and exact saved-container cleanup remain intact.

A fresh, clean LF Linux checkout of that committed merge passed **219/219 tests,
zero skips, in 22.514 seconds** on Python 3.14.4. The remote subprocess regression
also passed separately. Ruff 0.16.10 and protected-file integrity passed. The Pi
adapter implementation is unchanged from the source used for the zero-spend
separated-credential gateway fixture above. Re-review of the merged head is pending.

The 218-test receipts and earlier packet remain historical. The merge changes
neither the 14 historical measurements and their source/input identities nor the
ordered kept-patch chain 001 → 013. Target GPU validation and the final registry
manifest digest remain outstanding.

The subsequent merge `e441448d5938ebe220935d54137a2df635ccfdc5` also retains
remote commit `d50e44378858cdca0aed1aa57cdf0b719a81912b` and its recorded legacy
container regression. That test mocks and asserts strict absence verification.
The existing exact stop and fail-closed absence check remain the sole legacy
cleanup path, avoiding a duplicate best-effort stop after a prefix sweep.

A fresh, clean LF Linux checkout of this committed merge passed **220/220 tests,
zero skips, in 53.766 seconds** on Python 3.14.4; the new recovery regression
passed separately. Ruff 0.16.10 and protected-file integrity passed. The production
harness module tree is unchanged from `c61748a`, including the Pi adapter tested
by the earlier zero-spend gateway fixture. The 218/219-test receipts and all
historical measurements remain unchanged. Re-review of the new head is pending.

## Healthy GPU preflight producer correction

An independent producer/consumer check found that the GPU inventory's `name`
evidence collided with `Report.add`'s first parameter. A healthy GPU observation
therefore raised `TypeError` and became a failed preflight. The parameter is now
`check_name`; the public `evidence.name` field remains unchanged. A regression
uses fake sysfs/devices and actual report production for both ROCm-SMI and the
AMD-SMI fallback, including the subsequent driver check.

Committed source `bed51c38c81afe79fd6041cbc6eed430c3a03749` passed **221/221 tests,
zero skips, in 20.073 seconds** in a fresh native Linux checkout on Python 3.14.4.
Ruff 0.16.10, protected-file integrity and validation of all 14 historical rows
passed. The checkout preserves committed bytes, including the protected third-party
CRLF notice; harness Python source is LF. The earlier remote fixes remain ancestors.
These are local software checks with simulated GPU inventory, not target inference.
Historical measurements and earlier frozen packets remain unchanged. Review of
the new head is pending.

The README now correctly states that `--out` is required; the other evidence
paths can default under `--state-dir`. Documentation source
`f7e27dbe9a6d2037b54e145903d26aafef5d88cd` passed a fresh native Linux check:
**221/221 tests, zero skips, in 21.313 seconds**, with locked Ruff 0.16.10,
protected integrity and all 14 historical rows passing. Its implementation hash
is unchanged from `bed51c3`; only the README and the preceding verification note
differ. Earlier measurements and frozen evidence remain intact.

## 2026-10-27 prospective proposer and retention corrections

The default program now describes the actual prompt-stratified geometric-mean
bootstrap, six sequential prompts with individual token ceilings, unchanged serving
profile and context contract. It corrects the inactive telemetry declarations and
requires one variable per patch. No hypothesis is a preselected optimization.

The controller supplies the actual current Dockerfile, selected benchmark, stock Pi
profile, runtime binding and selected baseline catalog in the retained prompt. A
catalog entry alone does not prove which model snapshot is mounted; independent
model preflight remains required. The Dockerfile's build-example comment now uses
the actual repository-root context. Runtime flags are unchanged.

Each prospective Pi attempt retains its prompt, sessions, stdout JSON transcript,
separate stderr and atomic attempt-status receipt under the existing external state
directory's `proposer/pi-attempt-*`. Receipts record process exit/timeout/abort and
known accounting status, including failed attempts; a killed controller leaves its
last durable stage. The manifest discovers these receipts independently of whether
a proposal reached an experiment report. Provider response bytes belong to the
configured gateway's separate retention path and must be exported with this tree.
`--provider-response-dir` / `PiLaunchConfig.provider_response_dir` records that
external operator binding in each attempt receipt and manifest, without claiming
that the directory exists or that moving files were verified. Stop the gateway,
snapshot its complete WAL, and verify the independent offhost export.
The harness cannot recover provider bodies it never received.

Startup completion admission is now capped at three probes, within the existing
startup deadline. Repeated invalid generations fail the attempt early. The worker
flushes each complete probe trace to its separate stderr capture, including the
successful probe, while preserving the result protocol on stdout. The parent
retains each worker's input, stdout and stderr in a unique external-state
`measurement-workers/worker-*` directory, including failures, timeouts and aborts.
The manifest discovers these files independently of completed experiment reports.
Readiness, correctness, metric arithmetic and candidate keep/revert rules are unchanged.

These are offline changes, not another genuine Pi trial or evidence of a latency
fix. The real baseline remains attributed to
`09f52c25af380a0daefdea3a183fe45a6ec75730`; no optimization gain exists. Historical
CPU evidence, missing fresh-revalidation report/30 traces, unretained timed-out body,
unresolved API reservation and frozen source/deployment packets are unchanged.
A future admitted run still requires final fresh validation, all 30 complete response
traces, verified offhost bytes and confirmed cleanup before resource teardown.
