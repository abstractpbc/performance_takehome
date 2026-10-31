# CANDIDATE-BUILT STAND-IN ENGINE (CPU)

This directory is **not** organizer-supplied. It exists because the real
target (LLMD on an MI355X) cannot be built or run in the development
environment: the LLMD source is closed, and no ROCm GPU host is available.

The stand-in is a real inference server (llama.cpp `llama-server`, pinned to
tag `v0.7.0` = commit `620d2225cc436ab0b001e3200e590e3b286ef9ab`, source
tarball SHA-256 `1d4a987d9bd048f6e93107e838026438df70fbde237fc0c69c63d600512b4bd9`)
serving a tiny pinned GGUF model over an OpenAI-compatible streaming API. It is
containerized with the same contract shape the harness uses for LLMD:

| Contract element | LLMD (organizer image) | Stand-in (this directory) |
| --- | --- | --- |
| ENTRYPOINT | `/llmd/llmd` | `/app/llama-server` |
| CMD (tunable runtime flags) | `--io-impl=zio` | see `Dockerfile` |
| Model argument appended by harness | `--model=/models/model` | `--model /models/model/model.gguf` |
| Model mount | `/models/model`, read-only | `/models/model`, read-only |
| Port inside container | 8000 | 8000 |
| Network | none / egress blocked | none / egress blocked |

## Model

`unsloth/SmolLM2-135M-Instruct-GGUF`, revision
`9e6855bc4be717fca1ef21360a1db4b29d5c559a`, file
`SmolLM2-135M-Instruct-Q8_0.gguf` (144,811,072 bytes), SHA-256
`c4a3dd037301b6ecea31d6da37f5cd793ead920dd5ddfe6d589294628d6ce66a`.

Fetch it with (writes `model.gguf` into a directory of your choice and verifies
the hash; nothing is committed):

```sh
uv run --frozen python tasks/harness-design/harness/run.py fetch-standin-model --dest /path/to/standin-model
```

## Experiment surface

Every `ARG` in the `Dockerfile` (compiler/ISA/LTO/OpenMP flags) and the `CMD`
line (threads, batch sizes, KV-cache types, flash attention, parallel slots)
are genuine knobs. A proposer patch is a unified diff against this directory;
the harness applies it to a scratch copy and runs `docker buildx build` to
produce a fresh image per attempt.

The server's sampling settings are *not* changed by experiments: the harness
always requests `temperature=0`, and the golden-output correctness gate rejects
any change that alters greedy outputs for the benchmark prompts.
