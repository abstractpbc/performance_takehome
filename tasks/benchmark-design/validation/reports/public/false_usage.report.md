# Evaluation: fixture-false_usage-115 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 17 | 63 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.7443059879997236 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'usage_discrepancy': 16} |
| aa_control_b | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'usage_discrepancy': 16} |
| chat_short_open | rate=4rps | 1/16 | 0.09917 [0.09917, 0.09917] | 0.09917 | 0.0625 | False | 0.01724 | 58.01 | {'usage_discrepancy': 15} |
| closed_loop_sweep | conc=8 | 0/16 | - [-, -] | - | 0 | False | - | - | {'usage_discrepancy': 16} |
| generation_long_open | rate=4rps | 16/16 | 0.09621 [0.09175, 0.09949] | 0.1046 | 1 | True | 0.006437 | 155.4 | - |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
