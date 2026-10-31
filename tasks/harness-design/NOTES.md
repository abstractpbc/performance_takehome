# Task 2 — Canonical loop completed; verified batch 4 image retained

The [30 October canonical run](harness/reports/live-mi355x-20261030/README.md) completed a genuine stock Pi 1.0.0 model proposal on CPU, strict artifact verification, then the canonical `Harness.run()` fresh GPU baseline → build → paired measurement → KEEP, followed by a separate fresh selected-image final 30. The model authored `--batch-size=16` → `1` without manual patch repair. All 120 measured requests across the four phases passed all six correctness gates. Runtime source is pinned to `5c439274e2d3744da68b310c769c96b0c78bfb92`; later documentation is separate.

| New paired serial development comparison | Speedup |90% bootstrap interval| Supported claim |
|---|---:|---|---|
| Etalon FTGR |1.259329|[1.251227,1.266861]|Improved|
| TTFT |1.087023|[0.940236,1.094201]|No change established|

FTGR supported the canonical KEEP. TTFT's interval crosses1; its point estimate is not a demonstrated improvement. There is only one startup per arm, so no statistical startup gain is claimed. Fresh final 30 passed all six gates. These are local development results for the six-prompt serial workload, not an official score or proof of unseen-workload performance.

The CPU stage used the unchanged official Pi 1.0.0 distribution/Node 22.19.0 with a declared [one-response/no-tools overlay](harness/PI-ONE-RESPONSE.md), GLM-5.2, provider JSON-object mode, one response and output cap2048. Pi's original four-tool profile remains the reference; no tools were active in this overlay. The controller wrote exact parsed model fields; it did not choose or repair the patch. Raw SDK/provider text equality, normal completion, terminal usage and source/model compatibility were checked before the GPU. The GPU used the verified completed proposal with **zero coding-model calls** and carried its already settled $0.0144352 provider upper once. This is an explicit deferred CPU/GPU pipeline, not a claim that Pi tools or model inference ran inside the GPU phase.

## Recommended image

The submitted recommendation remains the separately measured and publicly verified **batch 4** image:

`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb`

Its [prior evidence](harness/reports/live-mi355x-20261028/README.md) includes a final 30, the frozen4/8 controller-neighbor mixed selection rule, all 25 anonymously streamed/hash-verified OCI objects, and an actual anonymous Docker pull/inspect/isolated CPU-help run in [hosted CI](https://github.com/sharonbasovich/performance_takehome/actions/runs/40794303749). Its reported serial TTFT1.077835/FTGR1.265443 comparisons and small supplemental burst/long-input checks remain scoped to that historical selection. No evidence from the new run establishes batch 1 superiority over 4; its gain is not multiplied into the earlier gain. The genuine model selected 1, while controller neighbors selected4. The manifest keeps those attributions separate.

The [exact batch 4 build recipe](harness/reports/live-mi355x-20261028/controller-neighbors/batch4/original-built-candidate.Dockerfile), source SHA, immutable raw metadata and publication receipts remain authoritative for the submitted image. The new [batch 1 built recipe](harness/reports/live-mi355x-20261030/original-built-candidate.Dockerfile), patch and raw image metadata are retained separately as the canonical-loop result. Its [experimental immutable 93f4b publication](harness/reports/live-mi355x-20261030/publication-note.md) also passed all 25 anonymous complete objects/5,923,167,708 bytes and actual Docker pull/inspect/CPU-help/owned-container cleanup in [workflow41051877610](https://github.com/sharonbasovich/performance_takehome/actions/runs/41051877610), exact head 41456da. This adds verified delivery for the genuine canonical result while the batch 4 recommendation stays unchanged. Model weights are external read-only mounts and are not distributed in these images.

## Current log, failures and replay

The current [`harness/results.tsv`](harness/results.tsv) contains the genuine two-row canonical baseline/kept attempt. Only its report/patch paths were normalized for repository use; the [exact native log and mapping](harness/reports/live-mi355x-20261030/result-path-map.json) preserve the source bytes and hashes. The old14-row CPU stand-in log is [byte-exact archived history](harness/reports/history-cpu-standin-20261024/README.md); its old FTGR and keep claims are invalid performance evidence.

The new [120-request packet](harness/reports/live-mi355x-20261030/README.md) retains the original worker reports, durable/fsynced30-request journals for each phase, four startup probe journals, fresh golden outputs, per-prompt samples, decision and source pins. Tests replay all six correctness gates and the stratified keep decision from this evidence. The prior packet's570 primary and 100 supplemental requests remain separate historical evidence. New tests do not upgrade historical orchestration into canonical execution.

[Attempt history](harness/reports/live-mi355x-20261030/attempt-history.json) preserves 13 full-tool research calls with no completed proposal ($0.5076162 upper), a malformed unified-diff response ($0.0144292), a prose-outside-JSON response ($0.0196708), and the valid JSON-mode response ($0.0144352). Failed responses were not reformatted or promoted. A digit-bearing evidence filename initially blocked the final sealer; a versioned private alias adapter retained original bytes and passed the unchanged public verifier. No model text, patch or clock changed. Costs are known attempt upper bounds, not a full billing reconciliation; earlier unknown liabilities remain retained.

Two earlier [baseline-only GPU histories](harness/reports/live-mi355x-20261030/baseline-only-history.json) remain failed optimization cycles: allocation 614111949 retained210 baseline/final benchmark traces with 7 provider requests ($0.1339892 upper), and614151330 retained90 baseline/retry/final traces with 10 requests ($0.2309182 upper). Neither completed a built/measured candidate pair; a late partial model edit was unmeasured. Their original source, closure, archive hashes and status are retained privately with sanitized public references. These counts remain separate from the current120 and prior 570+100; repeated context/resume archives are not summed as new measurements.

## Limits and preserved history

This pilot used configuration-only changes because source/build access for LLMD was unavailable in the supplied draft. That is a practical delivery restriction, not an original task prohibition or evidence that engine optimizations are exhausted. The organizer's private grader has not run. Supplied hardware/profile/timed release details remain draft; no official grade is claimed. Serial short-prompt results do not prove broad concurrency or long-input improvements. Startup sample sizes are insufficient for a statistical startup claim.

Runtime 5c's seeded bootstrap traverses prompt groups in input insertion order. The packet preserves original worker order for exact replay of its recorded intervals. A future version should sort prompt IDs and test JSON-key permutation invariance; it would be a new decision-policy version and must report any recalculated intervals separately, preserving this run's original decision and confidence bounds.

Complete private evidence was verified on the local computer before early GPU deletion. Provider GET404 was observed at `2026-10-30T11:45:11.075182Z` before the original unchanged cleanup deadline. Raw Pi sessions/reasoning/prompts, failed prose, provider WAL, shared accounting Journal and credentials remain private. No retained failure, missing 26 October fresh-revalidation trace, or timed-out provider-body gap is reconstructed.

The prior main manifest is preserved byte for byte as [submission.controller-batch 4-live-20261029.json](submission.controller-batch4-live-20261029.json), alongside earlier Pi1/Pi2/26 October/CPU snapshots. Prior notes are [NOTES.history-through-20261029.md](NOTES.history-through-20261029.md). See the [current manifest](submission.json), [provider setup](harness/PI-PROVIDER.md), [deferred-artifact contract](harness/PI-ARTIFACT.md), and [delivery policy](harness/DELIVERY-POLICY.md).
