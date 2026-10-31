# Evaluation: run-1793208572-8dafad — status **VALID**

> ENGINE: model (kind=unknown)


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 1455 | 1455 | 1453 | 2 | 0 | 0 |

## Startup: launch -> first VALID generation: median 27.81708213449997 s over 2 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=1rps | 90/90 | 0.06194 [0.06075, 0.06381] | 0.1104 | 1 | True | 0.01727 | 57.91 | - |
| aa_control_b | rate=1rps | 90/90 | 0.06068 [0.05636, 0.06306] | 0.1086 | 1 | True | 0.01752 | 57.07 | - |
| chat_short_open | rate=0.5rps | 90/90 | 0.06063 [0.05917, 0.06351] | 0.1133 | 1 | True | 0.01781 | 56.16 | - |
| chat_short_open | rate=1rps | 90/90 | 0.06287 [0.06012, 0.06994] | 0.1145 | 1 | True | 0.01781 | 56.16 | - |
| chat_short_open | rate=2rps | 135/135 | 0.06022 [0.05858, 0.06466] | 0.1141 | 1 | True | 0.01809 | 55.28 | - |
| chat_short_open | rate=4rps | 180/180 | 1.063 [0.5276, 1.521] | 3.336 | 0.8616 | False | 0.0198 | 50.52 | - |
| closed_loop_sweep | conc=1 | 90/90 | 0.06346 [0.05961, 0.06625] | 0.09437 | 1 | True | 0.01698 | 58.88 | - |
| closed_loop_sweep | conc=16 | 96/96 | 0.2023 [0.1645, 0.236] | 0.3377 | 0.9992 | True | 0.01897 | 52.71 | - |
| closed_loop_sweep | conc=32 | 142/144 | 3.853 [0.6116, 4.106] | 5.175 | 0.7522 | False | 0.02053 | 48.71 | {'invalid_output': 2} |
| closed_loop_sweep | conc=4 | 90/90 | 0.07638 [0.07339, 0.08071] | 0.1496 | 1 | True | 0.01752 | 57.07 | - |
| generation_long_open | rate=0.2rps | 90/90 | 0.0548 [0.0529, 0.05625] | 0.06174 | 1 | True | 0.01781 | 56.16 | - |
| mixed_open | rate=1rps | 90/90 | 0.09381 [0.0671, 0.1089] | 1.026 | 0.9466 | True | 0.06699 | 14.93 | - |
| rag_long_open | rate=0.25rps | 90/90 | 0.8959 [0.8418, 0.9082] | 1.194 | 0.8613 | False | 0.09091 | 11 | - |
| rag_long_open | rate=0.5rps | 90/90 | 0.9295 [0.8611, 0.97] | 1.169 | 0.648 | False | 0.08854 | 11.29 | - |

## A/A control: inconclusive (aa_control_a|rate=1rps vs aa_control_b|rate=1rps)
