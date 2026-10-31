# Evaluation: fixture-drop-111 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 51 | 0 | 29 | 0 |

## Startup: launch -> first VALID generation: median 0.7594267239996952 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 8/16 | 0.09436 [0.0895, 0.09904] | 0.09922 | 0.5 | False | 0.01809 | 55.28 | {'missing_done': 8, 'missing_finish_reason': 8} |
| aa_control_b | rate=4rps | 10/16 | 0.09654 [0.09444, 0.09753] | 0.09826 | 0.625 | False | 0.01806 | 55.36 | {'missing_done': 6, 'missing_finish_reason': 6} |
| chat_short_open | rate=4rps | 11/16 | 0.09882 [0.0967, 0.1024] | 0.1036 | 0.6875 | False | 0.01752 | 57.07 | {'missing_done': 5, 'missing_finish_reason': 5} |
| closed_loop_sweep | conc=8 | 13/16 | 0.09882 [0.09697, 0.1009] | 0.1025 | 0.8125 | False | 0.01752 | 57.07 | {'missing_done': 3, 'missing_finish_reason': 3} |
| generation_long_open | rate=4rps | 9/16 | 0.09701 [0.09035, 0.09978] | 0.1007 | 0.5625 | False | 0.006437 | 155.4 | {'missing_done': 7, 'missing_finish_reason': 7, 'invalid_output': 7} |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
