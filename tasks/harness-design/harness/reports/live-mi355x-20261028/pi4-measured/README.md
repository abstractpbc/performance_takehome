# Pi4 intent with controller pin repair: batch size 16 to 1

Stock Pi genuinely chose `--batch-size=1` for the serial development workload.
Its raw Dockerfile also accidentally corrupted the immutable base-image digest.
The unchanged canonical source guard rejected that raw file **before build**;
it has no image, request traces or performance report.

The parent explicitly authorized a mechanical repair: restore only the exact
original `ARG BASE_IMAGE` line and preserve Pi's chosen knob/value. The corrected
bytes equal the canonical baseline with only `--batch-size=16` replaced by `1`.
They passed the unchanged canonical source guard. The raw proposal and Dockerfile,
raw guard rejection, corrected Dockerfile and controller repair receipt are
retained separately under `pi/`. This is **Pi4-intent-with-controller-pin-repair**,
not unaltered model output or canonical unattended autocorrection.

| Artifact | SHA-256 |
|---|---|
| Raw rejected model Dockerfile | `eed21a634ccccfdb6c1b917eab5ddeead9e164af3c7f5cdabe0ed67fcf117e25` |
| Corrected and actually built Dockerfile | `5a8216e4ffae627fec2804cddb7fa9c69b0fa95880893fe9cab2db86710cf0bc` |
| Actual selected platform manifest | `9da45e7aee536492404a67637ac314f28f6b40c66d6f85a00b05381cba4c1517` |
| Actual raw selected config | `d0d3fe954573f671536d190852051c84b74ab20268613d485234c83a751a3f79` |

Control, candidate and fresh selected-final each completed 30 requests and all
six correctness gates. Reports, samples, 31 durable records including startup,
results and container logs are retained. The canonical prompt-stratified decision
was **improved**, `kept=true`:

| Metric | Speedup | 90% bootstrap interval |
|---|---:|---|
| TTFT | 1.0928777004 | [1.0777815299, 1.1177840190] |
| FTGR | 1.2687589301 | [1.2605170312, 1.2751745902] |

This is evidence for the frozen six-prompt serial development workload, not an
official grade or a claim across concurrent or long-input workloads. Startup was
24.445 s control and 30.566 s candidate: about 25% slower, below the 1.5× hard
regression gate. One observation per side does not establish a startup interval.
The corrected candidate's fresh selected-final30 passed, with startup30.552 s.
The parent's supplemental check found substantial six-request burst regressions:
wall time about1.6× and median TTFT about5× worse. A long2086-token prompt was
about5% faster. All40 requests in eight groups passed six correctness gates.
This reinforces the serial-workload qualification. Exact supplemental evidence
is retained in `supplemental-probe/`. The final mixed selection uses the separately
attributed controller batch-size4 neighbor, not this batch-size1 image; see
[`../controller-neighbors/`](../controller-neighbors/README.md).

The external pipeline accidentally reused the old `feedback3` tag for the Pi4
image. All build/candidate/selected-final/export receipts bind immutable `9da45e7a`;
Pi3's archived candidate remains immutable `7a555bea`. Tag spelling is not an image
identity. Exact archived receipts are preserved without relabeling.

The cycle used external orchestration with canonical guard/measure/decision source
at `419a0bd07c598dcbaea80b757e74c938c75d8599`. It was not an unattended Harness run.
The offhost208,959-byte archive SHA-256 is
`7524674d2f8aa2bcb470f82ab60b2d6649453bfead566c13d0a1945d2e64a6af`.
Independent offline intake replayed all90 traces, six gates, exact TTFT/FTGR
samples and the bootstrap keep decision, checked the corrected source, and bound
the raw graph's23 inherited layer descriptors/diffIDs, Linux/amd64 platform,
entrypoint and environment. No raw graph bytes were changed for publication.

The raw config omits the old baseline's deprecated Windows-only `ArgsEscaped`
flag. Publication validation treats only that omission as compatible on the
verified Linux/amd64 JSON exec-array image; all other runtime fields and layers
remain checked. The exact raw config hash above remains authoritative.

Model metadata retains three tool starts/ends, final `stop`, stock Pi1.0.0 and
Node22.19.0 verification across1,243 package files, context/artifact hashes and
argument byte counts/hashes. Assistant reasoning, argument contents, tool outputs,
credentials, raw provider WAL and financial journals are excluded. The actual
six sanitized input files are retained under `pi/context/` as historical input,
separately from the refreshed current agent program.
