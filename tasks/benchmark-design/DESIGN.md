# Task 1 — Benchmark design report

## Evaluation goals and workloads

The benchmark measures three things: how long until the first token, the smoothness of the stream and how long a server takes to start producing.
We test with the following workloads:

| Cell            | Prompts                    | Output                     | Load                     | What it exposes                                                             |
| --------------- | -------------------------- | -------------------------- | ------------------------ | --------------------------------------------------------------------------- |
| `chat_light`    | 256 tokens                 | Exactly 256                | 25% of capacity          | Baseline TTFT and smoothness with no queueing                               |
| `bursty`        | 256 tokens                 | Exactly 256                | 25% of capacity, clumped | Short peaks the average rate hides: admission, batching, prefill contention |
| `long_prompt`   | 4096 tokens                | Exactly 256                | 25% of capacity          | Delayed prefill, which fast decode averages would hide                      |
| `long_output`   | 128 tokens                 | Exactly 1024, `ignore_eos` | 25% of capacity          | Decode stalls and use of slack                                              |
| `near_capacity` | 80% short, 20% long        | Exactly 256                | 75% of capacity          | Queueing, drops and unfair scheduling                                       |
| `prefix_hit`    | 4096 tokens, shared prefix | Exactly 256                | 25% of capacity          | Prefix-cache behavior                                                       |

Arrivals are seeded Poisson, so requests arrive randomly at a steady average rate, like real traffic.

We also have an additional `bursty` workload, which is `chat_light` with gamma gaps of shape 0.25 for bigger clumps, to test how the server smooths spikes. Every cell uses `ignore_eos`, so output length is set by the plan,
not by when the model chooses to stop. Without it, a model or engine change that writes shorter
answers would look like a speedup. A request that stops before
`max_tokens` is `truncated_output`, which also catches a server that ignores the flag. Canaries,
anchors and startup probes keep natural stopping, because their answers are read from the text.

Prompts are based on real text instead of sampled token IDs due to speculative decoding's acceptance rates based on content, and random inputs are not representative of real workloads, which would underestimate the server.

Instead, `collect/corpus.md` holds 36 original passages written for this suite, of
eleven kinds: emails, stories, explainers, code, meeting notes, policies, incident reports, product
docs, recipes, essays and reviews. A short prompt is one passage followed by a question that fits its
kind ("Draft a reply to the email above."). A long prompt is a RAG task, with a set of passages pasted in as documents, followed by a task over all of them.

`cold_fs` needs host rights to drop the page cache, so results report `warm_fs` only.

On the MI355X, `collect/sweep.py` measured each cell's capacity and set every `rate_rps`. The values
are in the MI355X table under the validation matrix.

## Metrics and validity

**TTFT** runs from the send time to the first event carrying generated text (either reasoning or content). Time to first content token is reported as well. Role-only and empty events never count.
A failed request stays in the population, censored at `request_timeout_s`, so failing slow requests
can only make the result worse.

**Fluidity and FTGR** use the shared Etalon code on token times, not chunk times. Streamed text is
retokenized with the pinned tokenizer. Tokens sharing an event share a time, so bursts stay visible and slack decides whether
they were too slow. A failed request scores fluidity 0.

**TPOT and TPS** are diagnostics only. Fluidity already judges streaming, so they're never gated or
compared, and they can't change a result. They're reported per cell to help explain one: TPOT and
request TPS at each reported quantile, plus system TPS.

**Startup** runs from launch start to the first probe that produces a valid completion. A readiness
response never stops the clock.

`suite/STATES.md` is the complete mapping. In summary:

| State          | Meaning                                                  | Examples                                                                                                    |
| -------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `valid`        | The evidence is complete and can be believed             | All gates pass                                                                                              |
| `invalid`      | The server broke its contract or the evidence is corrupt | Wrong canary, false usage, failures over 1%, crash, no valid startup, corrupt timestamps, vanished requests |
| `inconclusive` | Nothing shows a violation, but the evidence is too weak  | Too few requests for a quantile, client lag, too few anchors, no reference, unfinished warmup               |

An `invalid` beats `inconclusive`, which beats `valid`. Client-observed times and bytes
are trusted as the measurement. Everything the server claims is checked independently.

**Correctness uses two checks.** Canaries are seeded arithmetic prompts ("What is 5 + 5") to check for garbage outputs.
Anchors are fixed prompts compared with a reference answer from the same model: the first 8 characters,
the finish reason and at least half the length must match.

Anchors are loose because reasoning output often opens the same way, so they mostly catch placeholders.
Canaries are the stronger check, but they only show that the server answers arithmetic correctly under
load. A server that generates everything, then releases
tokens at a steady pace, can't be told apart from true streaming, though its cost appears in TTFT.

**The benchmark is fail-fast, to avoid extensive testing when something is guaranteed `invalid`**. This helps benchmarks stay fast when continuing testing would produce no useful output. Healthy runs always finish.

## Statistical policy

**Warmup:** each launch sends 20 requests that are excluded from metrics.

**Cache:** cells with unique prefixes force a miss; `prefix_hit` shares one. They're never pooled.
Canaries and anchors repeat exact bytes, so they count toward failure rates but not TTFT or fluidity.

**Repetitions** are the independent unit, since requests in one repetition share a queue. They're
spread across launches in a seeded order so launch effects don't pile onto one cell (`chat_light`, `prefix_hit`, etc).

|                      | Quick                               | Full                                |
| -------------------- | ----------------------------------- | ----------------------------------- |
| Cells                | 2                                   | 6                                   |
| Repetitions per cell | 5                                   | 8                                   |
| Window               | 120 s, or until 53 counted requests | 120 s, or until 53 counted requests |
| Launches             | 3                                   | 4                                   |
| Quantiles            | p50, p90                            | p50, p90, p99                       |

**Quantiles** are reported only when `n(1-q) >= 5`. p90 per repetition is what comparisons use, so a
repetition needs 50 counted requests or the cell is `quantile_underpowered`. p99 is reported when a
cell has 500 requests and left empty otherwise.

**Windows** keep running past 120 s until they hold 53 counted requests (50 plus 5% spare). Rates
scale with capacity, so slow cells get few requests in a fixed window: on the MI355X, three cells got
only 22–51 in 120 s.

**Comparison** uses a Welch t-interval with a Bonferroni adjustment on log p90 per repetition, mean
fluidity per repetition and log startup per launch. Each side needs at least 3 values. We rejected a
bootstrap, since 5–8 values give too few distinct resamples.

| Decision                     | Rule                                                                  |
| ---------------------------- | --------------------------------------------------------------------- |
| `regression` / `improvement` | The whole interval is past the margin                                 |
| `equivalent`                 | The whole interval is inside the margin                               |
| `inconclusive`               | The interval crosses a margin, or evidence is too weak on either side |
| `invalid`                    | Either run is `invalid`                                               |

Margins are 0.1 log for TTFT and startup and 0.02 for fluidity. An `inconclusive` run makes every
decision against it `inconclusive`.

**What the margins can resolve on the MI355X**, from the full run's spread between repetitions
(`evidence/analysis/calibrate.py`):

| Cell / axis              | SD per repetition (log) | Smallest regression, quick / full | `equivalent` reachable |
| ------------------------ | ----------------------- | --------------------------------- | ---------------------- |
| `chat_light` TTFT        | 0.039                   | 20% / 18%                         | Yes                    |
| `long_output` TTFT       | 0.045                   | 22% / 19%                         | Yes                    |
| `prefix_hit` TTFT        | 0.043                   | 21% / 19%                         | Yes                    |
| `bursty` TTFT            | 0.095                   | 35% / 30%                         | No                     |
| `long_prompt` TTFT       | 0.114                   | 41% / 35%                         | No                     |
| `near_capacity` TTFT     | 0.422                   | 171% / 129%                       | No                     |
| `near_capacity` fluidity | 0.019                   | +0.060 / +0.053                   | No                     |
| Startup                  | 0.040                   | 28% / 26%                         | No                     |

`near_capacity` queues, so its p90 swings about 50% between repetitions and only large changes show.
Launches added no visible effect.

The unpaired interval is wide even for identical runs, because it includes schedule differences
between repetitions. Pairing matching repetitions should tighten it, but we need an A/A run before
adopting it.

## Validation matrix (tests)

**Synthetic faults:** 47 types of errors are caught with 0 mismatches
(`tests/matrix.py`). They cover bursts, stalls, late first tokens, reasoning channels, Unicode
boundaries, errors and drops, wrong output, false usage, misleading readiness, and clean and noisy
controls.

**False positives:** 0 confident regressions in 300 A/A decisions (quiet and noisy controls, 30 pairs
each, quick mode).

**Detection** (quick mode, 30 synthetic pairs):

| Fault                           | Fault size → share detected          |
| ------------------------------- | ------------------------------------ |
| Slower TTFT                     | 5%: 0, 10%: 0, 20%: 97%, 50%: 100%   |
| Stalled requests (fluidity)     | 2%: 0, 5%: 0, 10%: 3%, 25%: 97%      |
| Slower startup                  | 5%: 0, 10%: 0, 25%: 77%, 50%: 100%   |
| Failed requests (run `invalid`) | 0.5%: 0, 1%: 77%, 2%: 100%, 5%: 100% |

Faults below the margin are meant to go undetected; they return `inconclusive` or `equivalent`.

**MI355X:** Qwen3.6-27B on `zmlai/llmd@sha256:0a2cd01a…` with only `--io-impl=zio --model=…`, in a
Daytona sandbox, full mode. All 4062 requests completed with none failed, and 1 of 207 anchors
diverged.

| Cell            | Capacity | Rate      | TTFT p90            | Fluidity | FTGR       |
| --------------- | -------- | --------- | ------------------- | -------- | ---------- |
| `chat_light`    | 3.33 rps | 0.831 rps | 116 ms (109–123)    | 1.000    | 55.3 tok/s |
| `bursty`        | 3.33 rps | 0.831 rps | 181 ms (157–209)    | 1.000    | 53.6 tok/s |
| `long_prompt`   | 1.38 rps | 0.346 rps | 1548 ms (1306–1836) | 0.963    | 19.7 tok/s |
| `long_output`   | 0.90 rps | 0.225 rps | 62 ms (57–66)       | 1.000    | 56.2 tok/s |
| `near_capacity` | 2.15 rps | 1.616 rps | 2223 ms (1180–4186) | 0.968    | 14.9 tok/s |
| `prefix_hit`    | 1.33 rps | 0.333 rps | 1537 ms (1440–1640) | 0.976    | 21.6 tok/s |

Startup was 35.3–38.6 s.

**Limitations:**

- No MI355X A/A pair or real slowdown fit the budget, so margins and `max_anchor_divergence` (0.1)
  are provisional.
- Decode at long context isn't isolated. `long_output` decodes at short context, and `long_prompt`
  decodes only 256 tokens. A 4096-token prompt with 1024 output tokens would test KV-cache growth,
  at about 55 more minutes per full run.

## Measured compute cost

| Run         | Wall time | Launch | Warmup | Requests | Drain | Cleanup |
| ----------- | --------- | ------ | ------ | -------- | ----- | ------- |
| MI355X full | 6265 s    | 149 s  | 98 s   | 5675 s   | 338 s | 3 s     |

Drain is measured separately, because an overloaded server keeps working after the windows end.

The MI355X full run took 1.74 GPU-hours, inside the plan's 2.5 h limit. New hardware also needs a
one-off setup: 844 s for the reference and 476 s for the capacity sweep.

| Mode  | Request time at MI355X rates | Wall limit in plan |
| ----- | ---------------------------- | ------------------ |
| Quick | 20 min (projected)           | 40 min             |
| Full  | 130 min                      | 2.5 h              |

With launches, warmup and drain, a full run takes about 140 minutes. Slower hardware stretches
windows further; `evaluate.py` flags a run over the limit as `over_budget`.

## Reproducibility

| Path                                      | Job                                                                      |
| ----------------------------------------- | ------------------------------------------------------------------------ |
| `evaluate.py`                             | Runs the analysis, writes `report.json`, prints the summary table        |
| `workload.json`                           | The benchmark plan                                                       |
| `STATES.md`                               | Rules for `valid`, `invalid` and `inconclusive`                          |
| `requirements.txt`                        | Dependencies for capture only; `evaluate.py` is stdlib-only              |
| `analysis/events.py`                      | Loads evidence and the plan; the only module that knows the event format |
| `analysis/gates.py`                       | Decides validity for requests and runs                                   |
| `analysis/axes.py`                        | Computes TTFT, fluidity, FTGR and startup per cell                       |
| `analysis/compare.py`                     | Intervals and comparison decisions                                       |
| `collect/capture.py`                      | Stand-in for the organizer's runner; also generates the reference        |
| `collect/sweep.py`                        | Measures each cell's capacity on a running server and sets `rate_rps`    |
| `collect/tokenize_events.py`              | Independent token counts and times                                       |
| `collect/corpus.md`                       | Original passages the prompts are built from                             |
| `collect/make_prompts.py`                 | Generates the seeded prompt sets                                         |
| `prompts/`                                | Generated prompts                                                        |
| `schemas/`                                | Plan and event formats                                                   |
| `tests/synth.py`                          | Synthetic evidence with injected faults                                  |
| `tests/matrix.py`, `tests/sensitivity.py` | Expected states, detection and false-alarm rates                         |
| `tests/test_*.py`                         | Unit tests                                                               |
| `tests/e2e/`                              | Full pipeline against a fake server and against real LLMD                |

```bash
python tasks/benchmark-design/suite/tests/matrix.py
python -m unittest discover -s tasks/benchmark-design/suite/tests
python tasks/benchmark-design/suite/tests/sensitivity.py
python tasks/benchmark-design/suite/evaluate.py --events <dir> \
  --workload tasks/benchmark-design/suite/workload.json --out report.json
# Rates for new hardware, from suite/, against a running server
python collect/sweep.py --workload workload.json --base-url http://127.0.0.1:8000 \
  --out <dir> --write-plan workload.<gpu>.json
```

| Identity                | Value                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------ |
| `workload.json` SHA-256 | `c9470814d97e2cddf10b81e14a6b0ae13ecd67ff997f435804772e42754e0058`                   |
| Seed                    | 20261030                                                                             |
| MI355X image            | `zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f` |
| MI355X model            | `Qwen/Qwen3.6-27B`, revision `6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`              |
| Prompt tokenizer        | `Qwen/Qwen3-8B`, revision `b968826d9c46dd6066d109eabc6255188de91218`                 |
| Output tokenizer        | The model's own: `Qwen/Qwen3.6-27B` at the revision above                            |

**Evidence** is in `evidence/`, with checksums in `SHA256SUMS`.

| Path            | Contents                                                                |
| --------------- | ----------------------------------------------------------------------- |
| `mi355x-full/`  | Raw results, plan, reference, `report.json` and run scripts             |
| `mi355x-fill/`  | A short check of the window rule on the three slowest cells             |
| `synthetic/`    | Output of `tests/matrix.py` and `tests/sensitivity.py`                  |
| `analysis/`     | `calibrate.py` (margin table) and `rules_mi355x.py` (correctness rules) |
| `daytona/`      | Sandbox driver and watcher, no credentials                              |
| `integrity.txt` | Integrity check output                                                  |

Token files are left out (146 MB) and regenerated from the raw SSE. To re-score the run from the
repository root:

```bash
E=tasks/benchmark-design/evidence S=tasks/benchmark-design/suite R=rescore
mkdir -p $R && tar xzf $E/mi355x-full/results.tgz -C $R
ln -s "$PWD/$S/prompts" $R/out/prompts
python $S/collect/tokenize_events.py --events $R/out/full \
  --repository Qwen/Qwen3.6-27B --revision 6a9e13bd6fc8f0983b9b99948120bc37f49c13e9
python $S/evaluate.py --events $R/out/full --workload $R/out/workload.mi355x.json --out report.json
```

This reproduces the committed `report.json` exactly, token file included. The plan needs `prompts/`
beside it, or canary answers are missing and the run scores `invalid`.

**Stand-ins:** the organizer's runner, schemas and token adapter weren't released, so
`collect/capture.py` and `schemas/` are our own. `evaluate.py` only depends on the event format in
`analysis/events.py`.

**Integrity:** `python -B -m tools.check_integrity --base 81aead8` passes. The `takehome-start` tag
doesn't exist, so we pass the starter commit directly.

## Effort and resource use

| Resource            | Use                                                                      |
| ------------------- | ------------------------------------------------------------------------ |
| Own MI355X, Daytona | 2.86 h: full run with setup (2.23 h), plus shorter partial runs (0.63 h) |
| Active hours        | 10                                                                       |
| API charges         | $25, for the MI355X time                                                 |
