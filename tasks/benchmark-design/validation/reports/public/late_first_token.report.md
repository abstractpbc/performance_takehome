# Evaluation: fixture-late_first_token-104 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 2.7471633660002226 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 2.103 [2.036, 2.198] | 2.283 | 0.4153 | False | 0.01805 | 55.41 | - |
| aa_control_b | rate=4rps | 16/16 | 2.142 [2.114, 2.215] | 2.279 | 0.409 | False | 0.01781 | 56.16 | - |
| chat_short_open | rate=4rps | 16/16 | 2.103 [2.032, 2.138] | 2.174 | 0.4165 | False | 0.01752 | 57.07 | - |
| closed_loop_sweep | conc=8 | 16/16 | 2.116 [1.999, 2.155] | 2.202 | 0.4164 | False | 0.01752 | 57.07 | - |
| generation_long_open | rate=4rps | 16/16 | 2.129 [2.021, 2.211] | 2.231 | 0.8771 | True | 0.006437 | 155.4 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
