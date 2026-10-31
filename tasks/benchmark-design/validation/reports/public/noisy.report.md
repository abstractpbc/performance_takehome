# Evaluation: fixture-noisy-101 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.6611548119999497 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.1247 [0.09492, 0.1392] | 0.1605 | 1 | True | 0.01886 | 53.03 | - |
| aa_control_b | rate=4rps | 16/16 | 0.108 [0.09551, 0.1199] | 0.1636 | 1 | True | 0.01943 | 51.48 | - |
| chat_short_open | rate=4rps | 16/16 | 0.1183 [0.07574, 0.1611] | 0.1841 | 1 | True | 0.01951 | 51.25 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.07377 [0.05775, 0.1208] | 0.1723 | 1 | True | 0.01987 | 50.34 | - |
| generation_long_open | rate=4rps | 16/16 | 0.1025 [0.08124, 0.1171] | 0.1381 | 1 | True | 0.006679 | 149.7 | - |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
