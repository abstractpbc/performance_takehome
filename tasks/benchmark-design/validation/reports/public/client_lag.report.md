# Evaluation: fixture-client_lag-901 — status **INVALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement

- INVALID: client event-loop lag 0.400s exceeds 0.05s: timings are client-owned

## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.7537175950001256 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.096 [0.09452, 0.09852] | 0.1014 | 1 | True | 0.01776 | 56.3 | - |
| aa_control_b | rate=4rps | 16/16 | 0.09682 [0.09463, 0.09973] | 0.1008 | 1 | True | 0.01776 | 56.3 | - |
| chat_short_open | rate=4rps | 16/16 | 0.09617 [0.09369, 0.09785] | 0.09994 | 1 | True | 0.01776 | 56.3 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1005 [0.09685, 0.1041] | 0.1051 | 1 | True | 0.01752 | 57.07 | - |
| generation_long_open | rate=4rps | 16/16 | 0.09899 [0.09643, 0.09994] | 0.103 | 1 | True | 0.006437 | 155.4 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
