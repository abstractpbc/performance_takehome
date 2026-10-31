# Separate real-engine diagnostic — 30 October 2026

This prospectively frozen point diagnostic exercised recorder **1.1.1** against
protected LLMD/Qwen3.6-27B on one AMD MI355X. It supplements the
[28 October full capture](../../REAL-RUN-20261028.md); it does not replace that
run or repair its missing historical lag evidence.

| Arm, in execution order | Offered | Actual outcome |
| --- | ---: | --- |
| Clean control A | 3 | 3 completed |
| Delay first visible SSE content frame by 100 ms | 3 | 3 completed |
| Replace an actually echoed nonce | 1 | Failed: `invalid_output:nonce_missing` |
| Close downstream after its first visible frame | 1 | Unfinished: missing done/finish reason and invalid short output |
| Clean control B | 3 | 3 completed |

An extra startup-generation probe completed. All 11 planned study requests were
offered: nine completed, one deliberately failed and one deliberately unfinished.
Both fault outcomes remain in the population.

Median TTFT in the delayed arm exceeded the six pooled clean controls by
**102.879 ms**, descriptive 95% percentile-bootstrap interval **92.704–108.962 ms**
(clean n=6, delayed n=3; 2,000 resamples). This tests this injected delay at this
point. Tiny n, fixed ordering, a shared host and IID bootstrap do not establish
power, population coverage or a general clean-control false-positive rate.

Client lag had **1,113 samples**, maximum **1.890 ms**. The independent raw timing
audit passed. All 12 upstream HTTP responses (including startup) completed and
full bodies were retained, including after intentional downstream close. The
ten clean/delay upstream and downstream bodies are byte-identical; raw fault
transformations and monotonic events are retained. No new recorder patch was
made for this study. This does not certify old boundary-lag coverage or prove
that every host disturbance is observable.

A separate wrapper launch-to-first-valid-completed-generation point was
**24.507899 s**. The wrapper owned the new physical container/process; recorder
`cold=false` stays unchanged because it attached to the proxy. The original
evaluator therefore remains **inconclusive** (exit 3: zero runner-owned cold
launches); runner and timing audit exit 0. The wrapper point is not substituted
into the evaluator. No host page-cache-cold, speedup, power or official grade is
claimed.

## Evidence and clocks

[`capture.tar.gz`](capture.tar.gz) is the exact unrecompressed 61,812-byte export,
SHA-256 `ecee5b425f202fd54158b642ba2c2ef0c88f78d60d87b9d326cf680fa651245d`.
All 65 payload hashes and the full offhost body were verified. It contains the
original run/requests, upstream/downstream SSE, injection events, logs, report,
timing audit, owned cleanup and internal manifest. No credentials, Pi sessions,
puzzle material or model weights are included. [`results.json`](results.json)
is a sanitized index; original reports are unchanged.

Capture/owned cleanup ran **06:20:07.479791–06:20:45.988868 UTC**; offhost hash
verification completed **06:20:47.318183 UTC**. Exact owned container absence and
an empty running-container list were checked before Task 2 resumed. Model,
runtime and Docker setup had already been performed for Task 2. This study made
no additional model download or model API request. Its short capture interval is
not an exclusive allocation cost or a new end-to-end setup/cleanup budget result.

This is **separate later work outside the original 28 October three-hour claim**.
The original full-suite start, files, reports, failed attempts and GPU-absence
receipts stay unchanged. This experiment never moves their clock.

## Reproduction

[`STUDY-PLAN.json`](STUDY-PLAN.json) and [`workload.study.json`](workload.study.json)
were frozen before capture. [`study_driver.capture.py.txt`](study_driver.capture.py.txt)
is the exact wrapper used for capture (SHA-256
`60cfdad3b79d881859939a6f63666675032fe2e61310e6f8e109d502e5022868`);
[`fault_proxy.py`](fault_proxy.py) is the unchanged fault transport.
[`SOURCE-PINS.json`](SOURCE-PINS.json) binds the exact recorder/evaluator/protected baseline bytes at
`96a9fa5a716d54a9700d14627fe184dec4e7dedc`. `manifest.json` binds this packet.

The current [`study_driver.py`](study_driver.py) corrects completion labelling for
future execution/replay: runner and timing checks must pass, evaluator exit 0 or
3 is required, all planned arms must be offered exactly once per request ID, and
a valid startup generation must exist. Aborted or incomplete studies retain
partial evidence and exit unsuccessfully. Nine CPU regressions cover those
failures and verify that this actual complete capture, including its intentional
faults and inconclusive evaluator, remains acceptable. This later correction was
not used during the GPU capture and changes none of its recorded bytes or values.

Verify every archive member, without extraction (stdlib only):

```bash
PACKET=tasks/benchmark-design/validation/engine-diagnostic-20261030
python -B "$PACKET/study_driver.py" --verify-export "$PACKET/capture.tar.gz"
```

CPU replay uses the existing evaluator and timing audit after safe extraction
into a fresh directory, with the extracted workload and pinned target tokenizer.
Follow the [original replay commands](../real-run-20261028/README.md), replacing
its workload/events paths with this archive's. Match Python 3.14.4, Unicode 16.0.0
and Tokenizers 0.22.2 when comparing reports. Retain `inconclusive` and both fault
outcomes; local path fields differ. No GPU or model weights are needed for replay.

For a new engine-backed run, use the exact target model/image in `results.json`,
matching dependencies and one otherwise idle assigned AMD GPU. Use an absolute
Linux source checkout with the byte-pinned files (Windows newline conversion is
not a match). All output/archive paths must be new:

```bash
python3.14 -I -S -B "$PACKET/study_driver.py" --execute \
  --source-root "$ROOT" --model-dir "$TARGET" --tokenizer-snapshot "$TARGET" \
  --python-site "$SITE" --image-id "$ACTUAL_BASE_IMAGE_ID" \
  --render-device "$ACTUAL_AMD_RENDER" --hard-deadline-utc "$ORIGINAL_WORK_STOP" \
  --out "$NEW_OUTPUT" --export "$NEW_ARCHIVE_OUTSIDE_OUTPUT"
```

The driver refuses running Docker containers, a pre-existing engine-port
responder or fewer than 480 seconds before the unchanged work-stop. Cleanup and
guest export are reserved inside the maximum 480-second study window. Ports
19181/19180 bind loopback only. Partial results are retained; never reset a clock
or relabel a partial capture to make the study pass.
