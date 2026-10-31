# Agent program: one genuine optimization proposal (Task 2)

Choose your own hypothesis and write exactly ONE attributable patch. The controller
supplies the current Dockerfile, selected development benchmark, baseline image/model
catalog, runtime binding, stock Pi profile, budget and attempt history below. Use those
actual inputs, including any changed last-good Dockerfile. No optimization patch or
winning knob has been selected for you. Build, inference, measurement and keep/revert
belong to the harness; you never run Docker or servers.

## Scope and contract

The draft task allows engine, build, packaging and configuration changes. This
pilot's self-contained LLMD delivery path is restricted to configuration changes
because LLMD source/build access was not supplied. That is a practical restriction
of this implementation, not an original task requirement or evidence that all
other optimizations are exhausted.

For `engine=llmd`, the scratch build context is the repository root. Edit **only
`tasks/harness-design/Dockerfile`**, one knob per attempt (one flag or one ENV variable).
Preserve the current file's other bytes. The pinned image has no shell; no `RUN` steps.
Keep `/llmd/llmd`, `--model=/models/model`, port 8000, `--io-impl=zio` and
`--max-context-len=128000`. Do not lower the context contract because short prompts fit.
Do not change the model, weights, tokenizer, quantization, chat template, greedy
sampling, stop behavior, request profile, benchmark, measurement, gates or accounting.
Never special-case prompts or cache answers. Read no private evidence or credentials.
Do not install tools, make network calls or run inference yourself.

The renderer's baseline catalog identifies available pinned models; the runtime
binding names the mounted model directory and arguments. Do not infer which snapshot
was mounted from a catalog entry alone. Full file pins are separately preflighted and
retained by the runner. If essential context is missing, state that and stop.

The setup is already supplied. If a file check is necessary, batch only the actual
scratch Dockerfile and necessary pinned input checks in one read-only tool action.
Do not repeat a repository investigation. Read the history, choose your mechanism,
write your own patch and metadata, then stop. A concise prompt does not promise that
provider latency, response limits or billing have been repaired.

## Frozen LLMD workload

The controller must explicitly select
`tasks/harness-design/harness/development-benchmark.llmd.json`; the CLI default is a
different generic benchmark. Read the **selected** benchmark supplied below. Its
`llmd_closed_thinking_v1` profile fixes temperature 0, `top_k=1`, empty-thinking
assistant prefill `</think>\n\n`, `continue_final_message=true`, and
`add_generation_prompt=false`, identically for startup and measurement. This serving
profile and the stock Pi tool profile are separate contracts, neither a speed change.

The LLMD development workload is sequential, six short prompts × five repetitions.
Output ceilings are colors 48, capital 48, sum 16, haiku 64, explain 96 and list 48
(the top-level default 48 does not override individual ceilings). The startup probe
is “Say hello.” with four tokens. Each request has a 120-second timeout. Historical
baseline inputs were 15–27 tokens. This does not measure concurrent queueing or long
prefill; do not claim those benefits or change the benchmark to make a knob look good.

## Decision you are optimizing against

`decision.py::bootstrap_speedup` groups samples by prompt. Each prompt's ratio uses
the median over its repetitions: baseline/candidate for TTFT, candidate/baseline for
Etalon FTGR. The reported speedup is the geometric mean of those prompt ratios
(>1 is better). The seeded 2,000-resample percentile bootstrap resamples within prompt
groups and computes a 90% CI. A gain needs the lower bound above 1 and at least the
configured 3% point effect; a significant primary-metric regression vetoes a keep.
This is not a ratio of pooled per-repetition medians.

All correctness gates must pass: golden greedy outputs, accepted finish reasons,
complete output and streamed-token/usage accounting. One cold startup is measured per
side: more than 1.5× last-good startup is a hard regression gate. Startup improvement
alone is `no_change`. A kept candidate becomes the baseline for the next attempt.
Read error/regression history before selecting a neighboring change.

## Measured history and remaining hypotheses

Choose against this workload and history, one knob only. These are hypotheses, not
performance evidence or instructions to copy a prepared patch.

* The genuine native-drafter candidate built but failed during DFlash repository
  resolution before any measured request. Required assets were unavailable within
  the current no-download/no-egress scope. No native-drafter gain was measured.
* Pi's page chunk16-to64 and prefill chunk256-to1024 candidates each completed
  control30, candidate30 and fresh selected-final30 with all six gates passing.
  Both canonical comparisons returned `no_change`; the baseline remained selected.
  Actual input usage was14–27 tokens, below the original256 prefill threshold,
  disproving the latter proposal's possible two-chunk hypothesis for these inputs.
  The exact original proposals and results remain in the public evidence packet.
* Pi4 genuinely chose batch size16-to1, but its raw file also corrupted the base
  pin and was rejected before build. The controller restored only that immutable
  line, preserving the chosen knob/value. The corrected intent passed all90
  control/candidate/final requests and six gates; its serial development comparison
  improved TTFT1.0929 and FTGR1.2688, with startup about25% slower within the1.5×
  limit. A supplemental six-request burst check regressed wall time about1.6× and
  median TTFT about5×. Controller-derived4/8 neighbors were then fixed before
  measurement; both passed the separate mixed rule, whose point-score selected4.
  A new exact-image final30 after that choice passed all six gates. No general gain
  is inferred. This is explicitly a controller-repaired experiment, not unaltered
  Pi output or unattended recovery. Controller-derived neighbors are separate
  tuning attempts, not new stock-Pi choices.
* Cache/GPU memory fractions may affect allocation overhead; changing either risks
  startup failure or OOM. No benefit has been measured here.
* Larger prefill/token batches or more admitted requests have no demonstrated
  long-prefill/concurrency benefit on these short sequential prompts. CPU-supported
  syntax does not establish ROCm/model support or a GPU gain.
* Both telemetry ENV declarations are commented out in the original wrapper.
  Changing **one** declaration is one variable; uncommenting both is two variables.
  Their startup benefit is unmeasured and cannot alone earn a keep.

Use only documented pinned binary flags. Current values are in the supplied
Dockerfile. Verified help names include `--batch-size`, `--token-batch-size`,
`--max-context-len`, `--prefill-chunk-size`, `--page-chunk-size`, `--backend`,
`--gpu-memory-fraction`, `--cache-memory-fraction`, `--topk`,
`--speculative-drafter-model`, `--speculative-draft-count`, `--model-name`, and
`--listen`. `--io-impl=zio` is accepted but absent from help; invent no other value.
Keep backend `auto` unless your own supported alternative has a defensible mechanism;
do not add CUDA/TPU options. Help availability is not performance evidence.

Historical26October state: the baseline was measured at
`09f52c25af380a0daefdea3a183fe45a6ec75730`, and that day's genuine Pi attempt
failed at a120-second upstream cap before producing a patch. The old statement
that no Pi optimization cycle existed described that date. On28October genuine
Pi proposals were built and measured through external stage orchestration calling
canonical guard/measure/decision functions, with fresh selected-final measurements;
the canonical unattended loop was software-tested separately. The corrected Pi4
intent demonstrated a local serial development gain as described above; no official
or general-workload gain is inferred. Older CPU stand-in keeps were invalidated by an interval
adapter bug and are not LLMD improvement evidence. The missing historical fresh
revalidation report/traces and provider body cannot be recreated by new results.

## CPU stand-in

For `engine=standin`, use only its supplied Dockerfile and scratch build context.
Analogous knobs include threads, batch sizes and build ARGs. Preserve the same
correctness and attribution rules. Scripted/fake-provider CPU fixtures test harness
behavior; they are not genuine Pi work or target hardware performance evidence.

Write the renderer-specified unified diff relative to the scratch context (for LLMD,
`a/tasks/harness-design/Dockerfile` and `b/tasks/harness-design/Dockerfile`) and metadata
with `experiment`, `hypothesis`, `notes`. Identify intended metric, physical mechanism
and correctness risk; never assert an unmeasured gain. Then stop.
