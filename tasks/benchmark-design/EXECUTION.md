# Task 1 — self-contained execution and evidence workflow

The recorder, schema, fixtures, calibration and evaluator are candidate-built replacements
for the draft interfaces. They are runnable as supplied; no further organizer release is
needed for engineering execution. They are not an official runner, grader or approval.
Completed target captures are documented in [the 28 October results](REAL-RUN-20261028.md)
and the [separate 30 October fixed90 supplement](validation/engine-fixed90-supplemental-20261030/README.md).
The procedure below describes prospective captures; it does not replace those retained
results, their source identities, or their disclosed validation limits.

## Freeze and verify generation policy

The selected efficient plan uses the named `output_policy.request_profile` value
`llmd_closed_thinking_v1`. The common request builder appends exactly one assistant
message containing `</think>\n\n` and fixed `continue_final_message=true` /
`add_generation_prompt=false` fields to every startup, warmup, calibration and scoring
request. Generated user instructions remain unchanged. No arbitrary message/model/body
overrides or combination with request_options is allowed. An unknown profile or
output-policy field is rejected. Prompt length remains the approximate user-message
covariate; common template/prefill overhead is not claimed as exact engine token count.
Retain typed compatibility and fresh calibration; a small arithmetic probe alone does
not validate the workload.

For profile-absent historical plans, the candidate client also accepts fixed controls under
`output_policy.request_options`. The only permitted forms are a boolean
`enable_thinking` and `chat_template_kwargs: {"enable_thinking": <boolean>}`. If both
are supplied their values must agree. An absent or empty object preserves the existing
request bytes. Unknown fields and attempts to override model, messages, token limits,
streaming or other request fields are rejected. The same fixed options reach startup
probes, warmups, calibration and scoring; per-cell switching is not supported.

These are transport representations, not a claim that a particular server implements
them. First retain a compatibility probe against the pinned model/server showing the
effective final-content channel, required nonce or exact sequence and completion reason.
The observed default-reasoning startup attempt exhausted its 32-token budget in the
reasoning channel with empty final content and `finish_reason=length`. HTTP/SSE success
does not make that a valid generation or calibration. Raising only the startup budget
does not establish that the 16-token short-sequence calibration cell can complete.

Do not select a non-thinking policy from a synthetic transport test. Once real
compatibility is established, freeze the chosen policy in a new plan and retain new
source/plan hashes before fresh calibration. The plan digest includes those bytes and
each request records the SHA-256 of its actual serialized outgoing body. Preserve the
failed capture unchanged; it cannot be refit or relabelled as evidence for a changed
policy. This adapter changes only the candidate measurement client, with no changes to
the protected baseline, provided runner files or correctness gates. The candidate-owned
long-generation cap changes explicitly below to preserve the original sequence range.

The selected startup probe uses the deterministic `calibration_short` one-integer
source with a 16-token cap and requires a valid completed stop. It reports time to the
first tiny correct generation, not chat-response duration. The failed earlier 32-token
chat startup attempt remains historical evidence; its clock/results are not relabeled.

## Freeze the experiment before paying for a long run

Use `suite/workload.efficient.json` for the next pilot. It keeps chat, RAG, long generation,
mixed arrivals, open-loop rates, closed-loop capacity and two independent A/A controls.
Three interleaved repetitions and two cold launches remain in full mode. It removes:

- Repeated calibration from scored runs. `calibration` is a separate baseline-only mode.
- The fictitious five-second cache-flush term when using the default nonce fallback.

All four scored chat rates remain: A/A controls also use 1 rps, but the comparison
excludes controls, so they do not replace a scored 1-rps measurement group. Full mode
has 42 groups and 1,455 requests: fixed half-scale counts, each floored at 30, with 48
requests retained at concurrency 32. The reduction is an explicit plan choice, not a
statistical-power claim. Separate calibration has a short-sequence cell and a
RAG length grid, covering the actual deterministic quick/full prompt-length domain.

The long integer-generation source retains its original 200..320 endpoint range.
The pinned target tokenizer requires 1,172 tokens for canonical 1..320 plus newline;
the old 1,024-token cap could truncate a correct answer. Its cap is now 1,280, with
108 tokens of textual headroom. Other per-source caps and exact correctness gates stay
unchanged. This feasibility check is not proof of model accuracy or formatting.

The original `workload.json`, fixtures and reports remain historical and unchanged. The
new plan's durations are still arithmetic, not measurements or a necessary runtime.
The new plan opts into deterministic leading nonces for every chat, RAG and sequence
request, with prompt lengths recomputed after insertion. Historical prompts keep their
old bytes. A leading user-content nonce prevents identical user prefixes; common chat
template state and coincidental short token prefixes remain unflushed. Cache metadata
states that limit and never claims an engine cache reset.
Three repetitions/sample floors are starting policies, not a power guarantee. Pilot CI
widths, A/A noise, correctness populations, long-output service times and observed cold
loads must inform the frozen scoring plan. Do not change the plan midway through a scored
capture. Retain `inconclusive` whenever data cannot distinguish the declared effect.

The current unmeasured arithmetic is 16.3 minutes for calibration, 39.8 for quick and
133.8 for full. These are separate modes, not a claim that their 189.9-minute sum fits
one 180-minute experiment. Full mode's actual clock starts before its setup; calibration
and quick work also consume the controller's combined allocation/cash envelope. Stage
and measure the preparation once, retaining its time and artifact provenance; never hide
setup by resetting a clock during a full experiment.

`schema/request.schema.json`, `schema/run.schema.json` and `schema/README.md` freeze the
candidate raw format at version 1.0.0. The recorder stores raw SSE and monotonic receive
timestamps. It now refuses to truncate a previous plain/compressed capture.
Recorder 1.1.0 also bounds each request at 2 MiB raw SSE/8,192 events and the complete
request file at the loader's 256 MiB limit. Exceeding a bound is an explicit protocol
failure or incomplete capture; partial evidence is retained and never scored as complete.
HTTP-error bodies are closed without materializing an unbounded response.

## Controller and host prerequisites

Before creation, the controller must have a verified combined cash envelope, current quote,
an independent allocation deletion mechanism and an artifact-transfer path. These are
separate from this task-local suite. A metadata flag such as `--gpu-hours` is not a lease.
No old two-hour planning cap is a Task 1 requirement. The written full-suite ceiling is
**10,800 seconds including setup, capture, evaluation, export and cleanup**.

The controller records the actual suite start before setup and supplies it unchanged to
`capture.py`; earlier preparation consumes that same clock. Pass a stricter allocation/
cost deadline when appropriate. The wrapper converts the remaining wall-clock interval
once to a monotonic deadline, reserves cleanup/export time, bounds subprocesses/logs,
and reports actual total time. An aborted/late/incomplete capture is not a full-suite pass.

Stage Linux amd64, the locked HTTPX dependencies, pinned tokenizer runtime/assets, the
specified full model snapshot, protected thin-wrapper image and actual assigned AMD GPU
devices. Verify driver/model/weight provenance separately. A snapshot marker is bookkeeping,
not a checksum certificate. `capture.py` requires the observed image ID/platform, checks
the selected protected model marker and character devices, and records those facts without
pretending to validate model-internal token IDs or engine source provenance.

Image preparation and the full source/build recipe must identify the protected pinned
LLMD base. The local image ID passed here comes from that independently retained build
evidence; it is not assumed to equal a raw config or registry manifest digest.

## Bounded real capture

Run from the repository root on the assigned host. The placeholders below deliberately
require observed values. Use fresh private paths outside the checkout. The export path
must be outside the evidence directory. The controller must receive and verify the export
before releasing the GPU; creating an archive on GPU-local storage is not durable export.

```sh
python -B tasks/benchmark-design/suite/capture.py \
  --plan tasks/benchmark-design/suite/workload.efficient.json \
  --mode calibration \
  --model-dir /srv/abstract-pilot/models/target \
  --image '<STAGED_PROTECTED_WRAPPER>' --expected-image-id 'sha256:<OBSERVED_LOCAL_ID>' \
  --tokenizer-snapshot /srv/abstract-pilot/packet/tokenizer-snapshots/target \
  --python-site /srv/abstract-pilot/python-site \
  --render-device '<OBSERVED_ASSIGNED_RENDER_DEVICE>' \
  --suite-start-utc '<CONTROLLER_RECEIPT_BEFORE_SETUP>' \
  --hard-deadline-utc '<CONTROLLER_COST_STOP>' \
  --out /srv/abstract-pilot/evidence/task1-calibration \
  --export /srv/abstract-pilot/export/task1-calibration.tar.gz
```

The launch derives the protected `tools.baseline.start_command`, preserves device,
CPU/RAM/shm, bind and security flags, removes `--detach`, and adds lifecycle-only
`--rm`, `--pull=never` and a unique ownership label. It uses an immutable inspected image
ID rather than a mutable tag. Cleanup checks exact container ID, name, label and requested
image before removal, then checks absence. No other container/image is pruned or changed.
Before launch, it exclusively writes and fsyncs `ownership.json` and its directory.
The outer controller must retain that checkpoint and its hash: it records the exact
random name, owner label/value, immutable image ID and model path before any server
starts. After a hard-killed wrapper, inspect those identities and remove by the
observed full container ID, then independently prove absence. The final receipt
alone is insufficient recovery evidence because a hard kill can bypass `finally`.
Docker calls use a temporary empty client configuration and inherit no cloud/API keys.
`--python-site` forwards only an explicitly verified staged dependency directory, not
the caller's arbitrary Python path. A fully provisioned interpreter may omit that flag.

The wrapper runs the evaluator and `audit_timing.py`, which independently reconstructs
raw SSE content/TTFT/completion and startup-to-linked-completed-probe time without importing
the recorder, evaluator or shared metric implementation. Correctness and fluidity remain
separate evaluator checks. Evidence has an exclusive allowlisted archive and per-file hash
manifest; weights, credentials and arbitrary directory contents are excluded. Log and
decompressed evidence caps abort explicitly rather than producing invented observations.

## Fit once, freeze, then collect fresh scoring data

After receiving complete calibration evidence, run from `tasks/benchmark-design/suite`:

```sh
python -B -m bench.calibration \
  --plan workload.efficient.json --events '<CALIBRATION_EVENTS>' \
  --tokenizer-snapshot '<VERIFIED_TARGET_TOKENIZER>' \
  --out-plan '<NEW_FROZEN_WORKLOAD.json>' --out-receipt '<NEW_CALIBRATION_RECEIPT.json>'
```

The fitter requires a separate calibration-only mode, complete planned populations,
valid outputs/timestamps/startup, enough observations and five distinct length values.
It recomputes approximate prompt lengths from the recorded messages and enumerates
the deterministic quick/full population, refusing to freeze a curve whose observed
length domain does not cover it. Scoring also rejects any request outside the frozen
domain. Its nonnegative
length slope plus largest observed residual is an **empirical upper envelope**, not a
confidence or prediction bound. It records hashes, model/tokenizer identities, fit domain
and limitations. It refuses to fit scored quick/full data or overwrite prior outputs.

The x axis is the candidate prompt-length covariate, not exact engine/chat-template token
counts. Extrapolation outside observed lengths is refused. Mock fitting requires
`--allow-mock` and proves software only. Existing scoring evidence has a different plan
digest and cannot be made valid retrospectively by changing its deadlines.

Use the frozen plan for new quick controls and then the justified full run, with the same
bounded capture entry point and fresh output paths. Review all three axes (TTFT, fluidity/
FTGR and startup), correctness/completeness, client lag, A/A false alarms and uncertainty.
Full runtime/cost must be actual measurements; preserve partial evidence and failures.

Before freezing the scoring plan, record an explicit pilot decision:

1. Replace startup/service/output-length planning inputs with the retained baseline
   observations; include measured setup/teardown/export and a written safety allowance.
   Reject a full allocation whose conservative forecast exceeds its remaining suite or
   cash deadline. A favorable arithmetic forecast alone is insufficient.
2. Examine baseline A/A dispersion and bootstrap CI widths against the already declared
   minimum effects. Increase counts only when the conservative time/cash envelope permits;
   preserve all workload axes, both arrival models, independent A/A seeds, three
   interleaved repetitions and two full-mode cold launches. Freeze the revised counts,
   assumptions, deadlines and file digest before new scoring requests start.
3. Keep p99 and the two-launch startup tail descriptive. Thirty samples per cell and
   three repetitions do not establish a stable 1% tail or a power guarantee. If the
   measured uncertainty cannot resolve the declared effect, report `inconclusive`; do not
   lower effect thresholds or refit deadlines after seeing scored results.

## Receive and verify before teardown

Transfer the exact archive and its externally retained capture/completion receipt through
the existing strict-host-key SSH controller. Bound the transfer with the same original
allocation deadline; no retry can extend the cash or suite envelope. On the receiver:

```sh
python -B tasks/benchmark-design/suite/capture.py verify-export \
  '<RECEIVED_ARCHIVE.tar.gz>' 536870912
```

Pin the archive SHA-256 from the sender's export manifest and compare it on the receiver;
the internal manifest alone is not authentication. Verification streams hashes without
extracting, rejects duplicate/unsafe/nonregular members, and enforces byte bounds. Keep
the controller's receipt and measured transfer/deletion timestamps. The cloud allocation
must still be deleted by its independently enforced cost deadline even after export failure.

Final delivery updates `DESIGN.md`, measured runtime/cost, raw evidence references and
`submission.json` using the exact committed source/frozen plan. No official grading result,
true internal token timing, real-market data or absent source provenance is invented.
