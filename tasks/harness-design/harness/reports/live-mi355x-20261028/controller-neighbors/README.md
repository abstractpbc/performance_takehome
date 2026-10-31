# Controller-derived batch-size neighbors

Pi chose batch size 1. Its corrected intent improved the serial development
workload but substantially regressed synchronized six-request bursts. The
controller therefore fixed two additional values, **4 and 8**, before measuring
either. These values are controller tuning choices, not new Pi proposals.
No additional model API call was made.

`frozen-before-measurements/` preserves the exact policy, two Dockerfiles,
pre-measurement proposal receipts, portable deterministic generator/selector and
ScriptedProposer-compatible replay queue. The generator derives each candidate
independently from the exact baseline and changes only the batch-size flag.
The queue's availability does not claim an actual canonical unattended run.
The original `PREPARED.json` is included byte-for-byte (SHA
`be67cb80262ba534104d3c479e460ad7a6857b86b5994183e0675f1376b92e75`),
so the retained historical v2 preparation script has every pinned input it reads.

The policy was frozen at `2026-10-28T23:59:36.180698Z`, SHA-256
`1b46842f8f05d41a521115aac6952137843c7d2902fcd71089e86cef040ea45d`.
It requires the unchanged serial benchmark's canonical keep decision, all
correctness gates and startup limit, plus descriptive supplemental bounds:
each of two six-request bursts must have candidate/baseline median TTFT and wall
time at most 1.10; two 2,086-input-token long requests must have median TTFT ratio
at most 1.10. Missing or invalid evidence is ineligible.

If both qualify, the policy chooses the largest geometric mean of serial TTFT
and FTGR point speedups, with an exact tie favoring 8. If neither qualifies, it
selects the original batch-size16 baseline for the mixed workload. The earlier
batch-size1 serial keep remains unchanged as a separate result. Final delivery
requires another fresh 30-request serial correctness run of the selected image.

This is exploratory selection after observing the batch-size1 tradeoff. Two
bursts and two long requests are descriptive checks, not confidence intervals,
power estimates or proof of performance on unseen workloads.

## Actual result and final selection

Both candidates passed all 90 primary requests and six gates. Batch 4's canonical
serial speedups were TTFT 1.07783475 (90% CI [1.06736, 1.10846]) and FTGR 1.26544313
([1.25359, 1.27189]); batch 8's were 1.07363443 and 1.20724844. Both passed every
supplemental bound. The frozen score selected **4**, 1.16787781 versus 1.13848298.
This ranks observed point effects; it does not establish significance between 4 and 8.

Batch 4's two burst median-TTFT ratios were 0.976560/0.940913, wall ratios
0.848519/0.843093 and long-input TTFT ratio 0.968852. There are 60 actual requests
across baseline/4/8 supplemental arms. `mixed-probe/` retains every trace, per-arm
result, profile and container log. Recorded group wall times are checked against
individual request durations; independent group-boundary timestamps were not retained.

The root choice was recorded before `final-after-mixed/` launched exact image
`sha256:84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb`.
That new final 30 passed all six gates, with 31 durable records including startup.
Its actual Dockerfile SHA is
`e0c0683f7c82d63b637827038384ec2e130d0fbf91aaa94db58df1c006a4720a`,
and raw config is `1a19b238c6ce1448fed276005ff14e3425e845db266141deb5391c6a84159586`.
The delivery archive is 403,850 bytes, SHA
`5167b01c7bbd8d3347cfa585aec1a24673b0210470810c90303e45aa79fc8f1d`.
The exact selected image was published and all25 complete anonymous objects were
streamed/hash-verified on the pinned hosted checker. A separate anonymous Docker
pull/inspect/isolated CPU-help run passed, with owned help-container absence
confirmed. See [`../publication/VERIFIED.json`](../publication/VERIFIED.json).
The large bodies/image were not downloaded or rehashed on this local computer.

## Portable hybrid overlay

The exact planner `portable-overlay/bounded_neighbor_candidates.py.txt` has SHA
`01052a1281c04a0aaedc08350067e7e4ea1ce9c7c4e3aa5799ce8616c8430d74`.
Python can execute this source artifact directly despite its `.py.txt` suffix.
From the repository root, a new free source-only preparation can use:

```text
python -B tasks/harness-design/harness/reports/live-mi355x-20261028/controller-neighbors/portable-overlay/bounded_neighbor_candidates.py.txt --baseline tasks/harness-design/harness/reports/live-mi355x-20261028/pi4-measured/actual-source/baseline.Dockerfile --family-candidate tasks/harness-design/harness/reports/live-mi355x-20261028/pi4-measured/pi/candidate.corrected.Dockerfile --family-proposal tasks/harness-design/harness/reports/live-mi355x-20261028/pi4-measured/pi/proposal.json --harness-root . --out /tmp/task2-neighbors-new
```

That produces a newly time-stamped source-only policy/queue; it does not build,
measure, spend, or replace the historical frozen policy. The published historical
replay queue is separately `portable-overlay/scripted-neighbor-queue-v2.json`, SHA
`b2a141de7112ce40c366539f59fe42e18fc008d66e27911cf7dabe144b2dbaee`.
It supports generic last-good replay from either batch 1 or 16 and then 4→8.
The original queue's failure to match batch 1 is preserved; v2 fixes replay only,
not measured source, candidate values or the frozen mixed selection.

The actual canonical CLI options are `--proposer scripted --experiments <queue>`.
For any separately authorized future canonical run, use a new lease/state/evidence
directory and explicitly keep `--development-benchmark` set to
`tasks/harness-design/harness/development-benchmark.llmd.json`. The retained queue
can feed that existing proposer; the optional mixed selector remains a declared
controller overlay. This real evidence used external stages, not that unattended
integration. The v2 preparation script intentionally pins the historical frozen
input directory; do not claim it consumes a newly time-stamped policy unchanged.
