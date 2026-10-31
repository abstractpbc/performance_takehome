# Task 2 — submission guide

Start with [submission.json](submission.json), the machine-readable image, runtime,
build and evidence index. [NOTES.md](NOTES.md) explains the results and limitations.
The primary deliverable is this public `linux/amd64` image:

```text
ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb
```

This **batch-4** image was selected by the recorded controller-neighbor mixed-workload
policy and passed a fresh final 30-request check. The [original selection and publication
proof](harness/reports/live-mi355x-20261028/controller-neighbors/README.md) retains the
recipe, raw reports, all-layer anonymous hash verification and actual Docker pull/help.

The separately published [experimental batch-1 image](harness/reports/live-mi355x-20261030/publication-note.md)
is the genuine Pi-authored result from the later canonical optimization loop. That run
does not establish that batch 1 beats batch 4, and Pi is not credited with selecting 4.
Keep the two experiments, source pins and gains separate.

## Run the submitted image

The model is **Qwen/Qwen3.6-27B**, revision
`6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`, matching the protected
[baseline lock](../../baseline/lock.json). Its weights are excluded from the image and
must be materialized at the approved host location and mounted read-only at `/models/model`.
Use the assigned MI355X device, client network, CPU/RAM/shm and isolation constraints
from the [image contract](../../docs/IMAGE_CONTRACT.md); no credentials, installation
or runtime weight download is needed. [Host preflight](harness/HOST_PREFLIGHT.md)
documents the read-only checks before any inference allocation.

The entrypoint is `/llmd/llmd`. Its complete submitted runtime command is:

```sh
/llmd/llmd --io-impl=zio --batch-size=4 --token-batch-size=1024 \
  --max-context-len=128000 --prefill-chunk-size=256 --page-chunk-size=16 \
  --backend=auto --gpu-memory-fraction=0.9 --cache-memory-fraction=0.95 \
  --model=/models/model
```

When passing Docker arguments after the image, pass the full argument list above
without repeating the entrypoint. Docker replaces `CMD`; appending only `--model`
would drop the configured batch-size argument. The manifest's `runtime.args` is the
authoritative list. The service listens on port 8000 on the container interface.

## Rebuild the thin wrapper

From a canonical LF repository checkout, with the pinned base already cached:

```sh
docker build --platform linux/amd64 --pull=false --network=none \
  --build-arg BASE_IMAGE=ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:7db01160c57479b9d608b7c2e6fc4e6b5d438e53eb4d44dc9f3ab0441d1bb20d \
  -f tasks/harness-design/harness/reports/live-mi355x-20261028/controller-neighbors/batch4/original-built-candidate.Dockerfile \
  -t candidate .
```

This is the manifest's equivalent configuration-wrapper replay, not a promise of an
identical rebuilt digest. The archived Dockerfile is unchanged historical evidence;
use the path and base override above rather than its generic baseline-build comment.
No LLMD binary or model weights are rebuilt. The upstream binary/layers are pinned
by OCI digest; LLMD source and its source revision were unavailable in the supplied
draft, so no from-source engine-build or source-level optimization claim is made.

## Inspect and reproduce the evidence

All commands below run from the repository root. The tests and integrity check are
CPU-side checks; they do not create a GPU or call a coding-model API.

```sh
python -B -m unittest discover -s tasks/harness-design/harness/tests -t tasks/harness-design/harness -v
python -B -m tools.check_integrity --base 81aead87d78ed468c4c374fb42921471b44c3f80
```

See [harness/README.md](harness/README.md) for the runnable loop, dependencies and
separate live-run setup. Use fresh state/evidence paths; preserve the committed
experiment log and historical reports. The original stock Pi four-tool profile and
the declared [one-response/no-tools overlay](harness/PI-ONE-RESPONSE.md) are distinct.

The [30 October packet](harness/reports/live-mi355x-20261030/README.md) records genuine
stock Pi 1.0.0 CPU proposal → strict unchanged-artifact verification → canonical GPU
baseline/build/control/candidate/KEEP → fresh final 30. It used runtime source
`5c439274e2d3744da68b310c769c96b0c78bfb92`; later documentation is separate. All 120
measured requests and four startup probes are retained. Serial FTGR improved
1.259329×; TTFT's interval crossed 1, and one startup per arm supports no statistical
startup-gain claim. The recommended batch-4 image retains its own earlier evidence.

These are development results, not an official score. Short serial workloads and
small supplemental burst/long-input probes do not prove generalization. Abstract's
private evaluator runs after submission; access to it is unnecessary to submit.
Nothing in this guide claims organizer acceptance or a completed official grade.
