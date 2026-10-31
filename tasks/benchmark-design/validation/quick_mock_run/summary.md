# Evaluation: quick-mock-clean-7 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 242 | 242 | 242 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 1.5477329710001868 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=1rps | 30/30 | 0.09731 [0.09568, 0.0993] | 0.1044 | 1 | True | 0.01849 | 54.07 | - |
| aa_control_b | rate=1rps | 30/30 | 0.09864 [0.09506, 0.1004] | 0.1034 | 1 | True | 0.01829 | 54.67 | - |
| chat_short_open | rate=1rps | 30/30 | 0.09739 [0.09634, 0.09944] | 0.1021 | 1 | True | 0.01809 | 55.28 | - |
| closed_loop_sweep | conc=1 | 30/30 | 0.09716 [0.09559, 0.1001] | 0.103 | 1 | True | 0.01837 | 54.42 | - |
| closed_loop_sweep | conc=16 | 32/32 | 0.1054 [0.1024, 0.107] | 0.1097 | 1 | True | 0.01752 | 57.07 | - |
| generation_long_open | rate=0.2rps | 30/30 | 0.09638 [0.09488, 0.09799] | 0.1008 | 1 | True | 0.006721 | 148.8 | - |
| mixed_open | rate=1rps | 30/30 | 0.09928 [0.09673, 0.1114] | 0.1245 | 1 | True | 0.02702 | 37.01 | - |
| rag_long_open | rate=0.25rps | 30/30 | 0.1304 [0.1264, 0.1337] | 0.1392 | 1 | True | 0.02861 | 34.96 | - |

## A/A control: no_difference (aa_control_a|rate=1rps vs aa_control_b|rate=1rps)
