# Evaluation: fixture-misleading_readiness-116 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 3.590289847999884 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.09866 [0.09551, 0.09981] | 0.1019 | 1 | True | 0.018 | 55.54 | - |
| aa_control_b | rate=4rps | 16/16 | 0.09851 [0.09701, 0.09992] | 0.1012 | 1 | True | 0.01805 | 55.41 | - |
| chat_short_open | rate=4rps | 16/16 | 0.0971 [0.09294, 0.103] | 0.1037 | 1 | True | 0.01752 | 57.07 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1026 [0.09632, 0.1073] | 0.1116 | 1 | True | 0.01752 | 57.07 | - |
| generation_long_open | rate=4rps | 16/16 | 0.09907 [0.09655, 0.09972] | 0.1005 | 1 | True | 0.006437 | 155.4 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
