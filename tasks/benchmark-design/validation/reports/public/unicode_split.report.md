# Evaluation: fixture-unicode_split-107 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 1.8107953169997018 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.1003 [0.09516, 0.1028] | 0.1043 | 0.2263 | True | 0.06555 | 15.25 | - |
| aa_control_b | rate=4rps | 16/16 | 0.09754 [0.09623, 0.1012] | 0.1038 | 0.2278 | True | 0.06442 | 15.52 | - |
| chat_short_open | rate=4rps | 16/16 | 0.09811 [0.09672, 0.0998] | 0.104 | 0.227 | True | 0.06437 | 15.53 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1125 [0.1067, 0.114] | 0.1183 | 0.219 | True | 0.06466 | 15.47 | - |
| generation_long_open | rate=4rps | 16/16 | 0.1002 [0.09911, 0.1018] | 0.1023 | 1 | True | 0.02288 | 43.71 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
