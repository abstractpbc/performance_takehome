# Task 2 — Candidate optimization harness contract (draft)

You start from the [plain Pi 1.0 profile](../../../agent-harness/README.md), implement
its optimization loop, and choose development measurements. No enhanced Task 1 harness
or prebuilt optimizer is supplied. The main output is an image digest, not a benchmark score. The organizer's official benchmark is private and
runs offline after submission; it is not an input to your harness. `run.py` is intentionally
not a solved optimizer in this starter repository. All paths below are repository-root-relative.

The [30 October packet](reports/live-mi355x-20261030/README.md) records a completed
genuine stock Pi CPU proposal → strict completed-artifact verification → canonical
`Harness.run()` fresh GPU baseline/build/control/candidate/KEEP, followed by a
separate fresh final 30. The declared one-response/no-tools overlay produced the
unchanged model patch for batch 1; no model call ran on the GPU. FTGR improved1.259329
in the serial development comparison; TTFT's interval crossed1. All 120 measured
requests passed all six gates. This is a deferred CPU/GPU phase boundary, not a
continuous Pi/GPU process or an official grade.

The submitted recommendation remains the prior anonymously verified batch 4 image
with [separate controller/mixed-selection provenance](reports/live-mi355x-20261028/README.md).
The new model selected 1, not 4, and no new superiority comparison between them is
claimed. [NOTES.md](../NOTES.md) and the current manifest separate both results.
The main `results.tsv` now contains the genuine two-row canonical GPU log with only
public path normalization; exact native bytes are retained in the new packet.
The invalid original 14-row CPU stand-in FTGR/keep history is [archived byte-exact](reports/history-cpu-standin-20261024/README.md).

Example candidate-defined Pi invocation (requires the coding-provider key in
`ANTHROPIC_API_KEY` and the installed Pi executable; see the provider setup below):

```text
python tasks/harness-design/harness/run.py run \
  --engine llmd --model-dir /path/to/model-repo \
  --proposer pi --pi-bin /opt/pi/node_modules/.bin/pi \
  --pi-provider anthropic --api-key-env ANTHROPIC_API_KEY \
  --baseline baseline/lock.json \
  --development-benchmark tasks/harness-design/harness/development-benchmark.llmd.json \
  --budget-minutes 360 \
  --api-spend-limit-usd 200 \
  --state-dir /var/tmp/harness-state \
  --out tasks/harness-design/submission.json \
  --results tasks/harness-design/harness/results.tsv \
  --reports-dir tasks/harness-design/harness/reports \
  --patches-dir tasks/harness-design/harness/patches \
  --overwrite-evidence
```

(`--out` is required; `--results`, `--reports-dir` and `--patches-dir` default under `--state-dir`.
pointing any of them at git-tracked files is refused with exit 2 unless `--overwrite-evidence`
is given, so a casual run can never overwrite the committed evidence.)

You define the local measurement configuration/schema and implement its invocation.
No organizer benchmark manifest, grader binary, or score endpoint is supplied.
Runtime invocation is a subset of the Task 2 candidate allowance, not an extra grant.
Task 1 credentials/leases cannot extend it.

The organizer supplies credential/balance/lease interfaces before task release. A CLI
flag is not a hard API spend cap. The actual proxy and compute leases enforce limits.

## Required behavior

1. Start from the pinned plain Pi 1.0.0 distribution with isolated task configuration.
   Preserve the supplied baseline; put candidate orchestration/prompts/tools under this
   directory or a declared Pi-derived fork/overlay. Record base identity, enhancements,
   effective tool/resources, transcripts, and coding-model usage. Read model/runtime/budget
   inputs; consult `program.md`, source, profiles, server logs, and raw local measurements. Do not embed benchmark answers or request-specific shortcuts.
2. Establish a measured development baseline, including correctness/completeness checks.
   Keep its image as a recoverable last-good state.
3. Form a hypothesis, save the patch/config, build a fresh candidate image, and resolve
   its digest. Isolate one engine experiment at a time on the assigned GPU.
4. Measure using your own development workloads and checks. Explain the keep/reject
   policy, noise handling, and limitations. Profiles or smoke success alone cannot
   substantiate a performance claim. Local checks do not certify an official grade.
5. Log every attempt, including build errors, startup timeouts, correctness failures,
   reversions, and interrupted runs. Keep the image/log/patch/config artifacts needed for replay.
6. Recover without a human. Terminate child process groups/containers, clean temporary
   state, restore last-good image/config, and continue if time and credits remain.
   Never `git reset --hard` an arbitrary user worktree.
7. Check API balance, lease time, and remaining compute before each experiment. Reserve
   final build/validation/submission time. On exhaustion or SIGTERM return the last image
   that passed your development checks; do not start a run that cannot fit the remaining lease.
8. Write the task-scoped `--out` manifest with `task=harness-design`, final image digest,
   development measurement configuration/evidence, and provenance on exit. Keep source/build
   and manifest synchronized; a launch tag cannot masquerade as a digest.
9. Run the [protected-file integrity check](../../../docs/INTEGRITY.md) against the
   released starter and record the result. Candidate harness diffs are expected; changes
   to supplied infrastructure/tests are not.

No requirement to discover a magic improvement. A reliable bounded loop with honest
negative results is better than fabricated progress or an optimizer that needs rescue.

## Experiment log

`results.tsv` contains one row per attempt. `image_digest` may be empty if the build
failed. `api_usd_cumulative` is reconciled with organizer billing, not model-estimated
spend. `gpu_hours_cumulative` comes from allocation accounting, not utilization.
Missing metrics stay blank, never zero. TSV fields escape tabs/newlines; artifacts keep
full multiline descriptions. Store raw local reports outside this summary table.

Outcome values: `baseline`, `improved`, `no_change`, `regressed`, `gate_failed`, `error`,
`interrupted`. These describe **your development checks only**. `kept` is `1` or `0`;
a kept state must have an image and a passing local validation report.

The `local_ttft_speedup`, `local_ftgr_speedup`, and `local_startup_s` columns are optional
local summaries, not official metrics. If you use them, document their definitions and
workloads in the referenced `development_report`; add clearly named columns for other
local metrics. Retain distributions, repetitions, failures, uncertainty, and measurement
cost in raw reports. There are no official grader reports to attach during development.

---

# Candidate implementation (CANDIDATE-BUILT; everything below this line is the candidate's)

Before allocating inference/API work on a new host, run the read-only
[host preflight](HOST_PREFLIGHT.md). It reports PASS/FAIL/MISSING for actual GPU identity,
local Docker, resource capacity, pinned model bytes, Node/Pi artifacts and registry
configuration, without downloading weights or making provider calls.

The contract above is the organizer's draft; its example now explicitly selects the Pi
proposer and provider setup required by this implementation. The implementation lives
next to it:

| Path | What it is |
| --- | --- |
| `run.py` | CLI: `run`, `validate-results`, `fetch-standin-model`, `retag-last-good` |
| `abstract_harness/engines.py` | `Engine` abstraction: `LlmdEngine` (pinned `baseline/lock.json` image, same device/mount/security flags as `tools.baseline.start_command`, internal no-egress network instead of `--publish`) and `StandinEngine` (candidate-built llama.cpp CPU server, `../standin/`) |
| `abstract_harness/proposers.py` | `PiProposer` (pinned Pi 1.0.0, print mode, stock tools, isolation flags, key by env-var name only) and `ScriptedProposer` (deterministic queue; used for the historical main CPU ledger). The current genuine CPU proposal uses the declared one-response overlay and completed-artifact adapter; older runs used external orchestration. |
| `abstract_harness/measure.py` | development benchmark runner: TTFT, Etalon FTGR via protected `benchmarks.metrics`, startup-to-first-valid-generation, correctness gates |
| `abstract_harness/decision.py` | prompt-stratified bootstrap decision rule (`improved` / `regressed` / `no_change` / `gate_failed`) |
| `abstract_harness/loop.py` | the unattended loop: build -> start -> gate -> A/B measure -> decide -> keep/revert; checkpoints, SIGTERM/SIGINT handling, resume from `state.json` |
| `abstract_harness/budget.py` | pre-attempt guard: API USD, GPU-hours (= allocated x elapsed), lease deadline, reserved final window |
| `abstract_harness/docker_runtime.py`, `procs.py` | Docker/BuildKit wrappers, process-group aware timeouts |
| `abstract_harness/results.py`, `state.py`, `manifest.py` | exact `results.tsv` schema + vocabulary, atomic on-disk state, final `--out` manifest + integrity report |
| `development-benchmark.json` | the candidate development workload (prompts, repetitions, gates, thresholds) |
| `experiments/standin-queue.json` | the deterministic queue that produced the archived historical CPU log (includes fault injections) |
| `patches/`, `reports/` | per-attempt unified diffs and development reports written by the loop |
| `tests/` | task-local unittest suite |

## Run it

Stand-in engine on CPU (produced the archived historical log, not the current GPU result; needs Docker + ~2 GB disk,
network only for the one-time model download and the first image build):

```sh
uv run --frozen python tasks/harness-design/harness/run.py fetch-standin-model --dest /tmp/standin-model
uv run --frozen python tasks/harness-design/harness/run.py run \
  --engine standin --model-dir /tmp/standin-model \
  --experiments tasks/harness-design/harness/experiments/standin-queue.json \
  --state-dir /tmp/harness-state --out /tmp/harness-state/submission.json \
  --budget-minutes 120 --api-spend-limit-usd 0 --gpus 0 --standin-cpus 8
```

Everything this writes (`results.tsv`, `reports/`, `patches/`, `submission.json`, `state.json`)
lands under `/tmp/harness-state`; the checkout stays clean (`git status --short` is empty
afterwards). To *replace* the committed evidence instead, add `--results
tasks/harness-design/harness/results.tsv --reports-dir tasks/harness-design/harness/reports
--patches-dir tasks/harness-design/harness/patches --out tasks/harness-design/submission.json
--overwrite-evidence` (and `--no-cache` for real builds). `--manifest-only` rewrites
`submission.json` + `reports/build-provenance.json` + `reports/agent-provenance.json` from an
existing state directory without running attempts, keeping the state's `source_commit` and
`harness_tree_sha256` (this was used for the historical CPU manifest after metadata fixes; it is not how the current real-run result was generated).

Example continuous-provider canonical invocation (the completed packet above used the declared deferred adapter instead):

```sh
export ANTHROPIC_API_KEY=...             # never passed on the command line, never logged
uv run --frozen python tasks/harness-design/harness/run.py run \
  --engine llmd --model-dir /path/to/model-repo \
  --proposer pi --pi-bin /opt/pi/node_modules/.bin/pi \
  --pi-provider anthropic --api-key-env ANTHROPIC_API_KEY \
  --development-benchmark tasks/harness-design/harness/development-benchmark.llmd.json \
  --state-dir /var/tmp/harness-state --out /var/tmp/harness-state/submission.json \
  --budget-minutes 1380 --api-spend-limit-usd 40 --gpus 1 --gpu-hours 24
```

The key variable must have the provider's native name: `ANTHROPIC_API_KEY` for Anthropic,
or `OPENAI_API_KEY` with `--pi-provider openai`. `--api-key-env` selects the variable to
forward; it does not rename an arbitrary alias into a provider credential.

For a compatible custom provider, put a `models.json` in the task's Pi agent directory
with an explicit nonzero price table and an `apiKey` environment reference. Validate
streamed tool calls and terminal usage before optimization. Pi initializes missing
usage to zero: the harness rejects zero-priced, missing or invalid assistant usage,
preserves the known subtotal, records `api_accounting_unresolved` in state/provenance,
and exits 2. A restart remains blocked until billing is reconciled. Timeout or an
unexplained process exit also leaves accounting unresolved; an error response does
not prove a request was free. The pre-attempt estimate is advisory, so the API proxy
must enforce its own spending limit within an agent's multiple model/tool turns.

Promotion requires a valid same-attempt last-good control and complete comparable
evidence for both primary metrics. Missing control images or failed control correctness
end that attempt as `error`; missing/invalid metric groups or repetitions cannot turn into
a keep by being filtered out. Every configured prompt must have exactly the configured
repetitions for both primary metrics on each arm, in addition to `--min-samples`.
Stage process groups are cleaned after exit as well as on timeout, including
background descendants whose direct parent has already exited. Termination has a bounded
SIGTERM grace period before SIGKILL; cleanup is included in the configured reserve.

Kept contexts are immutable snapshots. An atomic state checkpoint changes the image,
context pointer and replayable completed row together; recovery finishes a committed
keep after a crash instead of pairing a new context with an old image. The manifest
emits the ordered chain of kept patches and marks whether it is complete. Git metadata
is excluded from snapshots/diffs; unchanged binary files are compared as bytes, while
changed binary files are rejected by the text-patch interface.

The state directory must be outside the engine build context, including after symlink
resolution. LLMD uses the repository root as its build context, so place `--state-dir`
outside the checkout (for example `/var/tmp/abstract-harness-state`). Invalid placement
is rejected before state files, builds or context copies can be created.

Each persisted run has distinct container, image and network names. A separate run lock
also prevents copied state from reusing that namespace concurrently. Locks remain held
until cleanup finishes. Queue position is aligned after interruption recovery. Budget
time advances with monotonic elapsed time even if wall time moves backward; a restart
with wall time behind its saved accounting checkpoint stops for lease reconciliation.

Send `SIGTERM` at any time: the loop finishes the current step boundary, logs the attempt
as `interrupted`, keeps the last-good image, writes the manifest and exits 3. Re-running the
same command resumes from `state.json`.

## Tests (one command, offline, no GPU; Docker-only tests skip when the image is absent)

```sh
uv run --frozen python -m unittest discover -s tasks/harness-design/harness/tests -t tasks/harness-design/harness -v
```

Before any GPU allocation, run the **actual stock Pi proposal preflight** on CPU.
It is inactive without an explicit execution switch. Install Pi in a separate
directory and retain its official 1.0.0 TGZ; the preflight verifies that archive's
pinned integrity and all 1243 installed files. Node >=22.19.0 and Linux are required.

```sh
uv run --frozen python tasks/harness-design/harness/pi_protocol_smoke.py \
  --execute-fixture --node /path/to/node \
  --pi-cli /path/to/pi/dist/bundle/cli.js \
  --pi-package /path/to/pi-coding-agent-1.0.0.tgz \
  --out /tmp/new-pi-complete-proposal-evidence
```

Four deterministic loopback responses drive real `read`, `edit`, and `write`
tools, followed by a completed terminal response. The production proposer must
recover a patch that applies and passes the existing one-knob source guard.
Actual first/follow-up wire requests are retained and checked by the integration
tests, including GLM/vLLM non-thinking options and explicit automatic tool choice.
The fixed preflight edit is an interface fixture, not a performance hypothesis.
No inference, provider credential, Docker or GPU is used in fixture mode.

For the actual-stock integration tests, set `ABSTRACT_PI_SMOKE_NODE`,
`ABSTRACT_PI_SMOKE_CLI` and `ABSTRACT_PI_SMOKE_PACKAGE` to those same verified
assets, then run `tests.test_pi_preflight` from this harness's unittest root.
Without the assets, those tests explicitly skip; they never substitute a local
model or claim provider validation. The tests also reproduce output-limit,
no-patch, missing-terminal and genuinely stalled-response failures. Only the
synthetic stall test uses a shorter timer; normal/live proposal completion gets
300 seconds.

[PI-PROVIDER.md](PI-PROVIDER.md) documents the separately authorized live preflight
through an existing loopback budget gateway, the supported stock-Pi configuration,
300-second completed-proposal deadline and negative-fixture failure statuses.
A passing live greeting or single tool-call fragment is insufficient: require
`verification.json` status `PASS` before renting a GPU. A partial edited context,
missing terminal usage or output-limit response remains a failed proposal.

A [30 October real-provider CPU result](reports/cpu-stock-pi-preflight-20261030/README.md)
records a completed stock-tool proposal exchange. It establishes compatibility
and contains no new GPU performance claim.

## Validate the log

Prospective Pi attempts retain `prompt.md`, `pi-events.jsonl` (stdout),
`pi-stderr.log`, `sessions/`, proposal files and atomic `attempt-status.json`
under `<state-dir>/proposer/pi-attempt-*`, including failures before a proposal.
The manifest lists status receipts even when no experiment report exists. A status
left at `running` after abrupt termination does not prove terminal usage or success.
The controller renders the actual current Dockerfile and selected benchmark with
the profile and baseline catalog; a catalog does not verify mounted model bytes.

Export the entire attempt tree with the configured gateway's provider-response
retention files before teardown. Set `--provider-response-dir` to the operator-owned
external gateway WAL directory so the attempt receipt and manifest retain that
binding. This option neither captures bytes nor hashes a moving tree. Snapshot the
complete WAL after the gateway stops, including failed/partial responses, and verify
the offhost export independently. The public adapter captures Pi output; raw provider
response retention belongs to that gateway. Final fresh validation and offhost
verification of the full report and all 30 LLMD response traces remain runner duties.
These prospective files do not replace the missing historical revalidation/provider
bytes and do not establish a real Pi optimization gain.

```sh
uv run --frozen python tasks/harness-design/harness/run.py validate-results --results tasks/harness-design/harness/results.tsv
```

Add `--no-cache` to a `run` invocation to force every image build in that invocation to be a
full uncached rebuild (recorded as `build.no_cache` in the attempt report); the harness exits 4
if another instance already holds `<state-dir>/harness.lock` or if `state.json` is corrupt
and no `.bak` checkpoint exists.
