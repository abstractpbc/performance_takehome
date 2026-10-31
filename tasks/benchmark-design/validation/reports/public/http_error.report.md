# Evaluation: fixture-http_error-110 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 55 | 25 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.7429401879999205 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 13/16 | 0.09831 [0.09353, 0.1003] | 0.1029 | 0.8125 | False | 0.01781 | 56.16 | {'http_error': 3} |
| aa_control_b | rate=4rps | 12/16 | 0.09865 [0.09475, 0.1022] | 0.1031 | 0.75 | False | 0.01781 | 56.16 | {'http_error': 4} |
| chat_short_open | rate=4rps | 9/16 | 0.09801 [0.09304, 0.1033] | 0.1044 | 0.5625 | False | 0.01831 | 54.63 | {'http_error': 7} |
| closed_loop_sweep | conc=8 | 9/16 | 0.1038 [0.09977, 0.1088] | 0.1092 | 0.5625 | False | 0.01752 | 57.07 | {'http_error': 7} |
| generation_long_open | rate=4rps | 12/16 | 0.09504 [0.09203, 0.09875] | 0.1007 | 0.75 | False | 0.006437 | 155.4 | {'http_error': 4} |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
