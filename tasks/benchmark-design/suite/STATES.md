# Result states

This is the spec for the `analysis/` modules and their tests.

- **`valid`:** the evidence is complete and trustworthy, so the number can be believed.
- **`invalid`:** the server broke the contract, or the evidence is corrupt. The result must not be
  credited as performance.
- **`inconclusive`:** nothing shows a violation, but the evidence is too weak or measurement
  conditions weren't met. No conclusion either way, and the server isn't charged.

The state is computed at each level below. At every roll-up, `invalid` beats `inconclusive`, which
beats `valid`. Every non-`valid` result carries reason codes, and a request keeps all that apply.
Nothing reads fixture names or labels.

## What we trust

| Source | Examples | Treatment |
| --- | --- | --- |
| Observed by the client | Receive times, raw payloads, whether `[DONE]` arrived | Trusted as the measurement |
| Claimed by the server | `usage`, `finish_reason`, model ID, the text, readiness | Checked independently |

Identity fields such as hashes and digests are copied into the report, not gated. Only comparison
uses them.

## Level 1: run evidence

Checked first. An `invalid` here stops analysis.

| Signal | State | Reason code |
| --- | --- | --- |
| Required file missing, unparsable, or missing required fields | `invalid` | `evidence_missing` |
| Timestamp non-finite, negative, or decreasing within a request | `invalid` | `timing_corrupt` |
| Planned request has no record, and capture didn't abort | `invalid` | `request_vanished` |
| `manifest.aborted` is set | `inconclusive` | `run_truncated` |

`tokens.jsonl` is a required file.

## Level 2: each request

Applies to `phase=measure` and `phase=probe`. Warmup requests are excluded.

**`valid` needs all of these:**

- HTTP 200 with `text/event-stream`, and no error event.
- At least one generated token, from `reasoning_content` or `content`.
- `finish_reason` is `stop` or `length`, and `[DONE]` was received.
- `length` means the independent token count reached `max_tokens`.
- Token count meets `min_output_tokens`, and reaches `max_tokens` when `ignore_eos` is set. Both floors
  allow the usage tolerance, because retokenizing can fall a few tokens short.
- Output passes correctness.
- Reported usage is within the larger of `usage_tolerance_tokens` and `usage_tolerance_fraction` of
  the independent count, because retokenized text can merge tokens (up to 3 of 256 on real LLMD).
  That count adds the
  reasoning tags LLMD generates and bills but strips from the stream: one for an opened `<think>`,
  one more when `content` follows it.
- Finished within `request_timeout_s`.
- Sent within `max_client_lag_s` of schedule.

A canary's last number must equal `expected`, because models show working around the answer.
Canaries the reference model itself gets wrong are never sent. Other prompts have no per-request correctness check, because
exact-output checks are only reliable as a rate (see below).

| Signal | State | Reason code | Effect | Fails run |
| --- | --- | --- | --- | --- |
| Non-200, connection reset, or error event | `invalid` | `server_error` | Failed: FI=0, TTFT censored if no token | No |
| No generated token | `invalid` | `empty_output` | Failed: FI=0, TTFT censored | No |
| Stream ended without `[DONE]` or finish reason | `invalid` | `incomplete_stream` | Failed: FI=0 | No |
| `length` or `ignore_eos` below `max_tokens`, or below `min_output_tokens`, beyond the tolerance | `invalid` | `truncated_output` | Failed: FI=0 | No |
| Past `request_timeout_s` | `invalid` | `timed_out` | Failed: FI=0, TTFT censored if no token | No |
| Canary wrong, or stray U+FFFD in the output | `invalid` | `wrong_output` | Failed | **Yes** |
| Usage disagrees with independent count | `invalid` | `false_usage` | Failed | **Yes** |
| Sent later than `max_client_lag_s` after schedule | `inconclusive` | `client_lag` | Excluded, counted | No |
| Capture aborted the request | `inconclusive` | `client_aborted` | Excluded, counted | No |
| Usage absent while `usage_expected` is true | `inconclusive` | `usage_absent` | Excluded, counted | No |
| Generated text has no matching tokens | `inconclusive` | `tokens_missing` | Excluded, counted | No |
| Canary has no known answer | `inconclusive` | `no_canary_answer` | Excluded, counted | No |

**Censoring:** a censored TTFT stays in the population at `request_timeout_s`. Failed requests can
only make TTFT worse, so failing slow requests can't improve a score.

**Populations:** canaries and anchors repeat exact bytes, so the prefix cache could flatter them. They
count toward request totals and failure rates, but never toward TTFT or fluidity.

**Output stability:** greedy output isn't stable under load. On real LLMD (Qwen3-0.6B, RTX 3060),
sequential anchors on a fresh launch matched the reference 24 of 24, but only 7 to 19 of 24 matched
with concurrent traffic or after it, as early as 13 characters in. So an exact match can't be
required. An anchor diverges only when its first `match_chars` characters differ from the reference,
its finish reason differs, or it's under half the reference length. Stability is judged as a rate
over stable anchors, at run level:

| Signal | State | Reason code |
| --- | --- | --- |
| Diverging share of stable anchors is at or under `max_anchor_divergence` | `valid`, rate reported | |
| Diverging share is over `max_anchor_divergence` | `invalid` | `reference_mismatch` |
| Reference absent, or for another model revision | `inconclusive` | `no_reference` |
| Fewer than `min_stable_anchors` stable anchors observed | `inconclusive` | `anchors_underpowered` |
| `max_anchor_divergence` not yet measured | `inconclusive` | `divergence_unmeasured` |

`max_anchor_divergence` is measured from baseline A/A runs, not assumed. A lying server that replaces
its opening or shortens output diverges on nearly every anchor, so it's still caught. Plausible text
that keeps the right opening, length and finish isn't caught here; canaries cover that instead.
Anchors look like normal requests on the wire, so the server can't single them out.

**Timing rules, not states:**

- TTFT starts at the first event with generated text. Empty, role-only and heartbeat events never
  count.
- A readiness response never stops the startup clock.

**Known limit:** a server that finishes generating, then releases correct tokens at a steady pace,
looks identical to true streaming from outside. The cost appears as higher TTFT. Timing can't be
forged, because receive times come from our client.

## Level 3: each axis, per cell

A run can be `valid` on TTFT and `inconclusive` on FTGR. "Failed fraction" is failed requests over
failed plus valid requests.

| Axis | `valid` | `invalid` | `inconclusive` |
| --- | --- | --- | --- |
| TTFT | Failed fraction within limit, and `n(1-q) >= min_valid_per_quantile` for each mode quantile | Failed fraction over limit (`failure_rate`) | Too few requests (`quantile_underpowered`) |
| Fluidity index | Same as TTFT, with token timing for each counted request | Failed fraction over limit (`failure_rate`) | Too few requests (`quantile_underpowered`) |
| FTGR | Every eligible trace resolves | Failed fraction over limit (`failure_rate`) | Any trace unresolved (`ftgr_unresolved`) |

With `min_valid_per_quantile` at 5, p50 needs 10 requests, p90 needs 50, and p99 needs 500.

An unresolved FTGR trace, such as all tokens in one event, is never treated as infinite speed or
dropped. A burst stays `valid`, because fluidity's slack accounting decides whether it hurt.

Startup is one axis per run, not per cell:

| Signal | State | Reason code |
| --- | --- | --- |
| Every launch reached a valid probe before `startup_timeout_s`, with at least 3 launches | `valid` | |
| Readiness seen, but no valid probe by the timeout | `invalid` | `startup_failed` |
| Server crash during startup | `invalid` | `startup_crash` |
| Wrong probe output | `invalid` | `wrong_output` |
| Fewer than 3 usable launches | `inconclusive` | `startup_underpowered` |
| Launch record cut short by capture | `inconclusive` | `startup_truncated` |

A server crash after startup makes the run `invalid` (`server_crash`), whether or not capture stopped.

## Level 4: whole run

| State | Rule |
| --- | --- |
| `invalid` | Any Level 1 `invalid`, any required axis `invalid`, output stability `invalid`, or any request with a run-failing reason. One is enough: speed can't offset wrong answers |
| `inconclusive` | Not `invalid`, and any of: Level 1, a required axis or output stability is `inconclusive`; more than `max_lagged_fraction` of requests hit `client_lag`; any `warmup` phase isn't `ok` (`warmup_incomplete`); no measured request is `valid` (`no_valid_requests`) |
| `valid` | Everything the plan requires is present and `valid`. An empty gate list is never `valid` |

Required axes are TTFT, fluidity and startup. FTGR is reported, and its state affects the run only
through `failure_rate`, so one unresolved trace doesn't erase a good TTFT result. Its own state stays
visible in the summary.

TPOT and TPS are diagnostics. They have no state, reason or comparison, and can't change a result.
TPOT is the time from a request's first to last token over its token gaps, so it ignores TTFT and
counts only completed measured requests. Request TPS is its inverse. System TPS is all completed
tokens in a repetition, canaries and anchors included, over that repetition's first send to last
token, averaged across repetitions.

Failures at or under `max_failed_fraction` leave the run `valid`, but they're charged and shown. On
small cells one failure can exceed the limit, which is deliberately conservative.

## Fail fast

`capture.py` stops early only when the run is already certain to be `invalid`, to save GPU time. A
healthy run always completes its full plan, and a good result never stops a run early.

| Trigger | Action |
| --- | --- |
| Server crash, or no valid startup probe by `startup_timeout_s` | Stop immediately |
| `max_run_failing_requests` requests with a run-failing reason | Stop |
| In one cell, failures exceed `max_failed_fraction` of the cell's planned requests | Stop |

`reference_mismatch` never triggers a stop, because it's a rate that needs the whole run.
`false_usage` can't trigger one either, because capture doesn't tokenize during a run, which keeps
CPU work out of the timing loop. Both are still judged afterwards, so such a run costs its full time. The stop
sets `manifest.aborted`, and the run stays `invalid` with its real reason, because `invalid` beats
`run_truncated`.

## Level 5: comparison, per axis and cell

| Decision | Rule |
| --- | --- |
| `invalid` | Either run is `invalid` (`run_invalid`), or model, plan hash, mode or cache state differ (`identity_mismatch`) |
| `inconclusive` | Either run is `inconclusive` (`run_inconclusive`), either axis is `inconclusive`, fewer than 3 repetitions on a side (`too_few_repetitions`), or the CI crosses a margin |
| `regression` | Whole CI is worse than the margin |
| `improvement` | Whole CI is better than the margin |
| `equivalent` | Whole CI is inside the margin |

A/B comparisons expect different image digests, so a different digest doesn't make a comparison
`invalid`. An inconclusive run can't support a decision even if its axes look fine, because its
measurement conditions weren't met.

