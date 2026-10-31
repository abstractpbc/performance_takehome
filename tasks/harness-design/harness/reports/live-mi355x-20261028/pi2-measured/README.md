# Pi2 measured feedback: page chunk 16 to 64, no change

The genuine saved Pi2 proposal changed one documented flag, `--page-chunk-size=16`
to `64`, without adding DFlash assets, weights, downloads, or filesystem layers.
The actual new GPU build was
`sha256:047fca293b9bba553ad3c22088eae41620c2895c31edb167eb2927f58e7502a7`.
This is distinct from the earlier CPU-only prebuild identity; no bit-reproducible
build claim or substitution is made.

Control, candidate and fresh selected-final each completed 30 requests over the
same frozen six-prompt / five-repetition `llmd_closed_thinking_v1` workload, and
all six correctness gates passed. Each has a retained full report, samples,
31-line durable trace file including startup, result receipt, and container logs.
The actual source and original built Dockerfile are retained verbatim.

The canonical prompt-stratified decision was **no_change**, `kept=false`:

| Metric | Speedup | 90% bootstrap interval | Decision |
|---|---:|---|---|
| TTFT | 1.0177859770 | [0.9936536207, 1.0529946081] | no_change |
| FTGR | 0.9971710029 | [0.9921540348, 1.0486389462] | no_change |

One cold startup was observed per side (32.089 s control, 31.023 s candidate).
That supports the startup hard gate, not a startup confidence-interval claim.
The selected baseline's new final 30 requests passed with startup 31.017 s.
No optimization gain, new published candidate, or official grade is claimed.

The selection remains the already public immutable baseline
`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:7db01160c57479b9d608b7c2e6fc4e6b5d438e53eb4d44dc9f3ab0441d1bb20d`.
Its exact selected config/platform metadata is retained in `selected-metadata/`.
No registry publication is needed for this no_change result.

This cycle was performed by the retained external orchestration using canonical
guard/measure/decision source at `419a0bd07c598dcbaea80b757e74c938c75d8599`.
It is not execution of the canonical unattended Harness loop. Original work and
cleanup deadlines were not extended. The export finished at 23:14:09Z; the
96,670-byte offhost archive SHA-256 is
`e3848e7ae2631372faa05e0bd2821923cef77be5be0a9668d2a984856d339280`.

An independent offline intake replayed every gate, TTFT/FTGR sample and canonical
decision from the 90 traces, and checked the selected graph's exact 23 inherited
layer/diffID identities and original Dockerfile. It returned
`BASELINE_RETAINED_ALREADY_PUBLIC_NO_NEW_PUBLICATION`, without registry, Docker,
GPU or provider actions. Further feedback attempts are separate future evidence.

`results.tsv` has the same separately declared four-phase index columns documented
in the parent README, with three measured rows here. It does not replace the main
historical 14-row CPU TSV or the first cycle's four-row index. All file hashes are
bound by the parent `inventory.json`.
