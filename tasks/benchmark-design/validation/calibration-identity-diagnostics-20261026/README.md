# Post-calibration software validation

This revision binds declared calibration to the run/model and verified tokenizer
semantics, checks cross-run plan/model/tokenizer consistency, and adds descriptive
bootstrap-tail and p99-support diagnostics. Candidate engine image and lock-digest
changes remain permitted. The decision algorithms, seeds, effects, resample count,
frozen calibration and historical raw events are unchanged.

The native software suite ran once: 192 tests, 191 passed, one unavailable historical
held-out-key skip. Both cached protected-model tokenizer snapshots were enabled.
The four executable source hashes match the implementation commit in the verification
receipt. The original test receipt preserves the earlier corrigendum hash; subsequent
prose only qualifies provenance normalization and trusted direct-API digest binding.

This is CPU/mock software validation. It supplies no real full/quick comparison,
measured GPU power/runtime or finite-sample family-wise coverage guarantee. The
real MI355X calibration remains attributed to source0174ca4 and its original clocks.
See ../../suite/INFERENCE-CORRIGENDUM.md for inference and API limitations.
