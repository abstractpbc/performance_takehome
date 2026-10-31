# Evaluation: fixture-stall-103 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 3.7608741250001003 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.09766 [0.09483, 0.09946] | 0.1015 | 0.3295 | True | 0.09028 | 11.08 | - |
| aa_control_b | rate=4rps | 16/16 | 0.09796 [0.0936, 0.1007] | 0.1024 | 0.3288 | True | 0.09081 | 11.01 | - |
| chat_short_open | rate=4rps | 16/16 | 0.09818 [0.09503, 0.1007] | 0.1025 | 0.3292 | True | 0.09052 | 11.05 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1071 [0.1059, 0.1092] | 0.1119 | 0.3279 | True | 0.09028 | 11.08 | - |
| generation_long_open | rate=4rps | 16/16 | 0.09676 [0.0943, 0.09916] | 0.1016 | 1 | True | 0.01521 | 65.77 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
