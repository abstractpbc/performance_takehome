# Evaluation: fixture-truncated_text-114 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 64 | 16 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.7649688859996786 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.09726 [0.09137, 0.09918] | 0.1024 | 1 | True | 0.01829 | 54.68 | - |
| aa_control_b | rate=4rps | 16/16 | 0.09501 [0.09264, 0.09717] | 0.09802 | 1 | True | 0.01805 | 55.41 | - |
| chat_short_open | rate=4rps | 16/16 | 0.09655 [0.09427, 0.1007] | 0.1031 | 1 | True | 0.01776 | 56.3 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1006 [0.09708, 0.1018] | 0.1028 | 1 | True | 0.01724 | 58.01 | - |
| generation_long_open | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'invalid_output': 16} |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
