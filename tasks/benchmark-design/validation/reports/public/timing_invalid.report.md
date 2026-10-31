# Evaluation: fixture-timing_invalid-900 — status **INVALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement

- INVALID: 7 request(s) with untrustworthy evidence, e.g. chat_short_open|rate=4rps|rep0/measure/6: ['invalid_timing:event[50]<event[49]']

## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 73 | 0 | 0 | 7 |

## Startup: launch -> first VALID generation: median 0.7537175950001256 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.096 [0.09452, 0.09852] | 0.1014 | 1 | True | 0.01776 | 56.3 | - |
| aa_control_b | rate=4rps | 15/16 | 0.09791 [0.09552, 0.1002] | 0.101 | 0.9375 | False | 0.01777 | 56.29 | {'invalid_timing': 1} |
| chat_short_open | rate=4rps | 14/16 | 0.09617 [0.09409, 0.09785] | 0.09879 | 0.875 | False | 0.01777 | 56.28 | {'invalid_timing': 2} |
| closed_loop_sweep | conc=8 | 14/16 | 0.09963 [0.09634, 0.1023] | 0.1042 | 0.875 | False | 0.01752 | 57.07 | {'invalid_timing': 2} |
| generation_long_open | rate=4rps | 14/16 | 0.09899 [0.09653, 0.1007] | 0.103 | 0.875 | False | 0.006437 | 155.4 | {'invalid_timing': 2} |

## A/A control: inconclusive (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
