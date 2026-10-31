# Evaluation: fixture-missing_done-108 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 1 | 0 | 79 | 0 |

## Startup: launch -> first VALID generation: median 0.7474217379999573 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'missing_done': 16} |
| aa_control_b | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'missing_done': 16} |
| chat_short_open | rate=4rps | 1/16 | 0.09837 [0.09837, 0.09837] | 0.09837 | 0.0625 | False | 0.01752 | 57.07 | {'missing_done': 15} |
| closed_loop_sweep | conc=8 | 0/16 | - [-, -] | - | 0 | False | - | - | {'missing_done': 16} |
| generation_long_open | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'missing_done': 16} |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
