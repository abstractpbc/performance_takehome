# Evaluation: fixture-slow_startup-117 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 3.169180141000197 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.09768 [0.09536, 0.101] | 0.1051 | 1 | True | 0.01776 | 56.3 | - |
| aa_control_b | rate=4rps | 16/16 | 0.09625 [0.0947, 0.1011] | 0.1032 | 1 | True | 0.01781 | 56.16 | - |
| chat_short_open | rate=4rps | 16/16 | 0.0954 [0.09414, 0.09797] | 0.1002 | 1 | True | 0.01752 | 57.07 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1003 [0.09663, 0.102] | 0.105 | 1 | True | 0.01752 | 57.07 | - |
| generation_long_open | rate=4rps | 16/16 | 0.09871 [0.09625, 0.1003] | 0.1014 | 1 | True | 0.006437 | 155.4 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
