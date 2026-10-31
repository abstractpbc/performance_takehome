# Task 2 — Measured batch size 4, verified public image

The final measured selection is controller-derived batch size 4:

`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb`

The exact image/config/source and passing final 30 are retained. **Publication,
all 25 complete anonymous objects and a separate anonymous Docker pull/inspect/
isolated CPU-help run passed** in [actual hosted CI](https://github.com/sharonbasovich/performance_takehome/actions/runs/40794303749).
The current [receipts](harness/reports/live-mi355x-20261028/publication/VERIFIED.json)
bind selected platform 84d and config 1a19. Historical baseline 7db proof remains
separate. The complete bodies were streamed and hashed on the pinned hosted
checker; they were not retained or rehashed on this local computer.

| Frozen serial development comparison | Speedup | 90% bootstrap interval |
|---|---:|---|
| TTFT | 1.0778347527 | [1.0673637364, 1.1084559661] |
| Etalon FTGR | 1.2654431344 | [1.2535909610, 1.2718943604] |

Control and candidate each completed 30 requests and all six correctness gates.
Startup was 24.881 s control and 32.486 s candidate, about 31% slower, within the
1.5× hard regression limit. One startup observation per side does not establish
a startup confidence interval. After the mixed-workload selection, a **new final
30-request run of exact 84d passed all six gates**, with startup 22.819 s.

The development profile remains `llmd_closed_thinking_v1`: six fixed greedy prompts,
five repetitions each. This is local proxy evidence, not an organizer grade or a
claim of improvement on all unseen workloads.

## How the selection was made

| Proposal or tuning step | Attribution | Actual result |
|---|---|---|
| Native DFlash drafter | Stock Pi | Built; startup repository-resolution failure; zero measured traces |
| Page chunk 16→64 | Stock Pi | All 90 control/candidate/final requests passed; `no_change` |
| Prefill chunk 256→1024 | Stock Pi | All 90 passed; `no_change`; actual 14–27 input tokens never bound the old threshold |
| Batch size 16→1 | Stock Pi intent, controller base-pin repair | Raw pin typo rejected before build; corrected intent improved serial performance but substantially regressed six-request bursts |
| Batch sizes 4 and 8 | Deterministic controller neighbors | Both passed the separately frozen mixed rule; 4 ranked higher under its declared point-score rule |

Pi genuinely chose the batch-size family at value 1. It did **not** choose 4 or 8.
Its raw Dockerfile also corrupted the immutable base pin. The controller restored
only that original line, preserving Pi's knob/value; raw rejection and corrected
source remain separate. The batch 1 serial keep remains unchanged as historical
evidence. Its six-request burst wall times were about 1.6× worse and median TTFT
about 5× worse, so it is not presented as an overall workload winner.

Before measuring either controller neighbor, the policy fixed the 4/8 grid and
selection rule at `2026-10-28T23:59:36.180698Z`, SHA
`1b46842f8f05d41a521115aac6952137843c7d2902fcd71089e86cef040ea45d`.
It requires canonical serial keep/correctness/startup plus candidate/baseline
ratios ≤1.10 for median TTFT and wall time in each of two six-request bursts, and
median TTFT for two 2,086-input-token long requests. Both qualified. Batch 4's
declared score 1.16788 exceeded batch 8's 1.13848; this is a ranking rule, not a
statistical claim that 4 is superior to 8.

Batch 4's burst median-TTFT ratios were 0.97656/0.94091 and wall ratios
0.84852/0.84309; its long-input TTFT ratio was 0.96885. All supplemental gates
passed. These small supplemental samples are descriptive. The neighborhood was
chosen after observing batch 1's tradeoff, so this is exploratory selection, not
independent confirmatory evidence.

## Software and real-run attribution

The real runs used **external stage orchestration** calling canonical source
guard, measurement and decision functions. The first cycle used source 99c006d;
later cycles used 419a0bd. They were not execution of the canonical unattended
`Harness.run()` loop. Its separate software tests do not change that attribution.
The final controller overlay and ScriptedProposer-compatible replay queue are
portable and retained; their availability does not retroactively make the run
unattended. See the [controller overlay](harness/reports/live-mi355x-20261028/controller-neighbors/README.md).

The task permits engine, build, packaging and configuration work. This pilot's
self-contained configuration-only delivery scope is a practical restriction
because LLMD source/build access was not supplied, not an original task rule or
evidence that other optimizations are exhausted.

The packet retains **570 primary measured requests and 100 supplemental requests**,
complete reports, durable traces, golden outputs, original built Dockerfiles,
build/metadata receipts and actual source. The first native-drafter candidate has
an empty trace file and no invented performance report. The final build source is
SHA `e0c0683f7c82d63b637827038384ec2e130d0fbf91aaa94db58df1c006a4720a`;
raw config is `1a19b238c6ce1448fed276005ff14e3425e845db266141deb5391c6a84159586`.
All 23 inherited layer descriptors/diffIDs and the entrypoint are preserved.

Stock Pi 1.0.0/Node 22.19.0 verification covered 1,243 package files. Public Pi
metadata contains only event/call IDs, sequence, permitted filenames, argument
sizes/hashes, stop reasons and runtime/artifact hashes. Assistant reasoning,
argument contents, tool output, credentials and provider WAL remain private.
Pi1's continuation is distinct from the earlier HTTP 529 and length-ended sessions.
Its requested 16,384 output cap was historically 8,192 on the wire; later sessions
used the corrected 16,384 cap. This history is not backdated.

## Delivery, cost and preserved history

Publication uses the exact retained config/platform metadata and the 23 already
public inherited blobs; missing blobs must fail rather than trigger layer copying.
No model weights or multi-gigabyte image archive is copied into this repository.
The actual anonymous whole-body and Docker receipts now support all delivery
verification flags. CPU help used an empty Docker configuration, no network,
no GPU/model mounts and a fresh labelled container subsequently confirmed absent.
No bit-reproducible-build claim is made.

The GPU was independently observed deleted after complete offhost export. Newly
settled model API cost through Pi4 is reported as **$0.5676140**. Earlier unresolved
full holds remain accounted for; missing usage is not assumed free. This is not a
billing reconciliation, and no private ledger, account/card data, provider key or
SSH material is published.

The old manifests remain byte-exact under
[submission.live-mi355x-20261026.json](submission.live-mi355x-20261026.json), its
[original Git blob](submission.live-mi355x-20261026.git.json), and the separately
preserved Pi1/Pi2 snapshots. Former notes remain in
[NOTES.history-through-20261026.md](NOTES.history-through-20261026.md). New evidence
does not reconstruct missing 26 October fresh-revalidation traces/provider bodies.
The original 14-row CPU `harness/results.tsv` is unchanged; its old FTGR/keep history
remains invalid performance evidence.

See the [current manifest](submission.json), [complete evidence/reproduction packet](harness/reports/live-mi355x-20261028/README.md),
[provider instructions](harness/PI-PROVIDER.md) and [delivery policy](harness/DELIVERY-POLICY.md).
