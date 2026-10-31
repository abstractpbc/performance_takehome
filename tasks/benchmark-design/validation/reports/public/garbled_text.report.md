# Evaluation: fixture-garbled_text-113 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 3 | 77 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.7231945109997469 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 1/16 | 0.08666 [0.08666, 0.08666] | 0.08666 | 0.0625 | False | 0.01809 | 55.28 | {'invalid_output': 22} |
| aa_control_b | rate=4rps | 1/16 | 0.09577 [0.09577, 0.09577] | 0.09577 | 0.0625 | False | 0.01667 | 59.99 | {'invalid_output': 20} |
| chat_short_open | rate=4rps | 1/16 | 0.0887 [0.0887, 0.0887] | 0.0887 | 0.0625 | False | 0.01752 | 57.07 | {'invalid_output': 19} |
| closed_loop_sweep | conc=8 | 0/16 | - [-, -] | - | 0 | False | - | - | {'invalid_output': 21} |
| generation_long_open | rate=4rps | 0/16 | - [-, -] | - | 0 | False | - | - | {'invalid_output': 32} |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
