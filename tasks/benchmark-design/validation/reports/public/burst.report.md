# Evaluation: fixture-burst-102 — status **VALID**

> ENGINE: mock/llm-d-stand-in (kind=mock) — CPU mock stand-in, NOT llm-d, NOT a GPU measurement


## Population
| planned | offered | completed | failed | unfinished | evidence_invalid |
| --- | --- | --- | --- | --- | --- |
| 80 | 80 | 80 | 0 | 0 | 0 |

## Startup: launch -> first VALID generation: median 0.7003814029999376 s over 1 launch(es)

## Cells
| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean | FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| aa_control_a | rate=4rps | 16/16 | 0.1828 [0.1799, 0.1855] | 0.1887 | 1 | True | 0.0161 | 62.11 | - |
| aa_control_b | rate=4rps | 16/16 | 0.1791 [0.1775, 0.1831] | 0.1854 | 1 | True | 0.0161 | 62.11 | - |
| chat_short_open | rate=4rps | 16/16 | 0.1814 [0.1797, 0.1848] | 0.1862 | 1 | True | 0.0161 | 62.11 | - |
| closed_loop_sweep | conc=8 | 16/16 | 0.1884 [0.1869, 0.1927] | 0.1975 | 1 | True | 0.0161 | 62.11 | - |
| generation_long_open | rate=4rps | 16/16 | 0.181 [0.1763, 0.1841] | 0.1869 | 1 | True | 0.005869 | 170.4 | - |

## A/A control: no_difference (aa_control_a|rate=4rps vs aa_control_b|rate=4rps)
