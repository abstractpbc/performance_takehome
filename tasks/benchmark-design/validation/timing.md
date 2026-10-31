# Measured wall time — CPU mock host (NOT GPU)

Host: `Linux-6.8.0-1061-aws-x86_64-with-glibc2.35`, Python 3.11.17. Mock delays scaled by time_scale=1.0.

- fixture generation (runner + cold-started mock, 18 public fixtures): 721.8 s total, per fixture: burst=30.97s, clean=31.83s, drop=30.82s, false_usage=31.93s, garbled_text=33.86s, http_error=29.25s, late_first_token=56.58s, misleading_readiness=34.68s, missing_done=31.83s, missing_finish_reason=31.89s, noisy=31.86s, reasoning_channel=39.35s, slow_decode=73.86s, slow_startup=34.19s, stall=68.02s, truncated_text=24.49s, unicode_split=74.6s, wrong_text=31.74s
- evaluate.py over 33 fixtures: 14.79 s total
- unittest suite: 42.12 s (exit 0; last line: `OK`)
- shipped `workload.json` quick mode, end-to-end against the CPU mock (`validation/quick_mock_run/`): 487.8 s wall (16 phases, run quick-mock-clean-7)

These numbers measure the candidate tooling on a CPU; they say nothing about llm-d or MI355X.
