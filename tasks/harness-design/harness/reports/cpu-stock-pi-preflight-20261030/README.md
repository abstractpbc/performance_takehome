# Real-provider CPU proposal preflight — 30 October 2026

A model-backed CPU preflight passed with unmodified official Pi 1.0.0 and Node
22.19.0. All 1,243 installed Pi distribution files matched the pinned official
archive. Six real model requests completed: stock `read`, `edit`, `bash`, `write`,
`write`, then terminal completion. The ordinary proposal deadline was 300 seconds.

This was a prescribed compatibility fixture: change only
`--token-batch-size=1024` to `256` in an isolated LLMD Dockerfile. The production
proposer recovered its unified patch, applied it in another scratch copy and
passed the existing `llmd-config-v1` one-knob source guard. The original source
build context remained unchanged. A separate read-only check confirmed the
successful write to the expected metadata file and valid, nonempty experiment,
hypothesis and notes; it did not repeat the model calls.

All six requests had complete terminal usage. The controller settled their
undiscounted API upper bound at **US$0.0280350**, before tax; the provider invoice
remains pending. Raw prompts, model reasoning, transcripts and private paths are
retained privately.

This establishes provider/tool/proposal compatibility. It is not an autonomous
optimization experiment, Docker build, GPU benchmark, speedup result or official
grade. Earlier incomplete GPU attempts remain separate evidence.

| Retained proof | SHA256 |
|---|---|
| Original live verification | `14c640dc3ea333e0eb6bcd8aa8cb1f81a10635b041dc50109f720e74335a64f7` |
| Read-only metadata augmentation | `f28e2965c766e9bae8ecaca9dee959ab5b7dc21188ea66bc06cdd580a94fa85b` |

See [provider setup and preflight instructions](../../PI-PROVIDER.md) for the
supported stock-Pi configuration, execution switches and failure handling.
