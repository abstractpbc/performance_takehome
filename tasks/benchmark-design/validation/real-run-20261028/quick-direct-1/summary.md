# Evaluation: run-1793207061-18e15f — status **VALID**

> ENGINE: model (kind=unknown)


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 242 | 242 | 242 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 25.28745089999984 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=1rps | 30/30 | 0.05966 [0.05446, 0.06911] | 0.1075 | 1 | True | 0.01784 | 56.05 | - |
| aa_control_b | rate=1rps | 30/30 | 0.06011 [0.05618, 0.06476] | 0.1059 | 1 | True | 0.01752 | 57.07 | - |
| chat_short_open | rate=1rps | 30/30 | 0.06183 [0.05724, 0.09491] | 0.115 | 1 | True | 0.01829 | 54.67 | - |
| closed_loop_sweep | conc=1 | 30/30 | 0.06427 [0.06136, 0.08012] | 0.09628 | 1 | True | 0.01724 | 58.01 | - |
| closed_loop_sweep | conc=16 | 32/32 | 0.2211 [0.1683, 0.2826] | 0.4172 | 0.9929 | True | 0.01942 | 51.49 | - |
| generation_long_open | rate=0.2rps | 30/30 | 0.05494 [0.05031, 0.05702] | 0.06082 | 1 | True | 0.01752 | 57.07 | - |
| mixed_open | rate=1rps | 30/30 | 0.1064 [0.06373, 0.1253] | 1.026 | 0.9836 | True | 0.02637 | 37.92 | - |
| rag_long_open | rate=0.25rps | 30/30 | 0.8692 [0.7787, 0.9869] | 1.187 | 0.8894 | False | 0.08343 | 11.99 | - |

## A/A control: inconclusive (aa_control_a|rate=1rps vs aa_control_b|rate=1rps)
