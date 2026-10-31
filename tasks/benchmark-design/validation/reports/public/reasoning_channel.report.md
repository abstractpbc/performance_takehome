# Evaluation: fixture-reasoning_channel-106 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 1.346253225000055 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.7043 [0.7012, 0.709] | 0.7092 | 0.789 | True | 0.01752 | 57.07 | - |
| aa_control_b | rate=4rps | 16/16 | 0.7056 [0.7043, 0.7096] | 0.714 | 0.7858 | True | 0.01781 | 56.16 | - |
| chat_short_open | rate=4rps | 16/16 | 0.7012 [0.6987, 0.7059] | 0.7079 | 0.7865 | True | 0.01752 | 57.07 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.7104 [0.7089, 0.718] | 0.7216 | 0.7795 | True | 0.01752 | 57.07 | - |
| generation_long_open | rate=4rps | 16/16 | 0.7043 [0.6993, 0.7124] | 0.7178 | 0.9721 | True | 0.006437 | 155.4 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
