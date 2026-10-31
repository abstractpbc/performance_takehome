# Evaluation: fixture-slow_decode-105 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 1.9315963849999207 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.1337 [0.1303, 0.1358] | 0.1384 | 0.1351 | True | 0.06746 | 14.82 | - |
| aa_control_b | rate=4rps | 16/16 | 0.1368 [0.1325, 0.139] | 0.1398 | 0.1304 | True | 0.06779 | 14.75 | - |
| chat_short_open | rate=4rps | 16/16 | 0.1319 [0.1306, 0.135] | 0.1394 | 0.1326 | True | 0.06693 | 14.94 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1364 [0.1331, 0.1396] | 0.1432 | 0.1306 | True | 0.0675 | 14.81 | - |
| generation_long_open | rate=4rps | 16/16 | 0.1333 [0.1318, 0.137] | 0.1428 | 1 | True | 0.02434 | 41.08 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
