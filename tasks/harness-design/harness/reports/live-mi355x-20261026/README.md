# Real LLMD development baseline

`000-baseline.json` is the original unmodified 45,430-byte report from source
`09f52c25af380a0daefdea3a183fe45a6ec75730`, SHA-256
`f83cadc074363d7affe2b830a45af96ecacc22e234f8857a9767a4accdc2edff`.
It records the exact startup/container command, original model outputs, usage and
finish gates, per-request timing and decision. `baseline-results.tsv` is the original
one-row baseline log. These are candidate development measurements, not an official
grade or optimization comparison.

Six workloads × five repetitions passed on the pinned Qwen3.6-27B snapshot and one
MI355X. Startup was 24.442 seconds; median TTFT approximately 0.0410 seconds and median
FTGR approximately 56.1619 tokens/second. The retained
[`fresh-revalidation-execution-receipt.json`](fresh-revalidation-execution-receipt.json)
reports a successful fresh 30-request revalidation against the selected image and
golden outputs, completed in 40.429 seconds with confirmed owned-container cleanup.
Its original full report hash is
`d1c197f3a4809af223b8c4d7536f78744df67ac25913b41b66158c91ca7f05b4`.
**That full report and its 30 fresh response records were not preserved offhost before
the GPU was destroyed.** The exact PASS execution receipt and hash remain, but those
fresh traces cannot be independently re-audited from the retained evidence. The
original 45,430-byte baseline report above is complete and retained.

Docker image-index identity:
`sha256:ccd73fc2f663734f222070502bca7928d62cfad1cee733170357e1c3dd957540`.
The config, platform manifest and index are distinct objects; see `submission.json`
and the [exact archive release](https://github.com/sharonbasovich/performance_takehome/releases/tag/task2-llmd-baseline-20261026).
Weights remain external. No bit-reproducible rebuild or full engine-source provenance
is claimed.

The failed genuine Pi attempt's transcript and partial harness records remain retained;
the old buffering gateway did not retain its timed-out upstream SSE body. The image was
exported, revalidated and reconstructed offhost; Devin verified every public archive
part before the disposable GPU was destroyed. Codex separately streamed and verified
the complete public archive after teardown, finishing at 10:05:04 UTC.
Delegated CPU import validation in
[PR 5](https://github.com/sharonbasovich/performance_takehome/pull/5) ran from
10:00:40 to 10:02:27 UTC: `docker load` preserved the image index, and the imported
image's `--help` invocation with network disabled exited zero. The root agent reviewed
that delegated evidence; it did not independently execute the import. This CPU check
does not establish GPU inference, registry publication or anonymous `docker pull`.
The shared GPU allocation's rate-ceiling estimate is $21.41 pending invoice, shared
with Task 1 rather than charged independently to each task. It does not establish a
successful Pi optimization cycle. The original export and complete cloud copy were
secured beforehand.
