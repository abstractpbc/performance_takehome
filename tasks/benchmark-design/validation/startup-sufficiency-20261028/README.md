# Complete cold-start evidence

The evaluator previously checked that some launch was cold and that some launch
completed a valid generation, but those could be different launches. A failed cold
start followed by a successful warm attach could therefore yield a valid report
with a warm-only startup median. A mixed pair could also replace one of the two
planned cold observations.

The corrected aggregate includes only cold launches with a gate-valid completed
generation. Fewer such observations than planned makes the run inconclusive;
per-launch failed and warm-attach timings remain visible. Two complete cold
observations retain their result. The candidate recorder already aborts its own
failed startups, so this is an evaluator acceptance fix, not an observed GPU failure.

Five focused regression cases cover complete cold starts, mixed cold/warm starts,
failed cold plus successful warm attach, all-warm evidence, and a failed second
cold generation. The focused startup run passed 15 tests. A separate full native
software run passed 196 of 197 tests, with the original opaque held-out key test
skipped because its private key was unavailable; no key or fixture was regenerated.

`verification.json` identifies the tested content by canonical LF file hashes and
the immutable base commit. The full run used a canonical base archive plus three
declared working-tree overlays, rather than claiming an as-yet-uncommitted future
commit. `FULL-NATIVE-RECEIPT.json` and `full-native-tests.log` retain that CPU run.
Protected-file integrity and changed-Python lint checks passed.

No historical raw events, calibration fit, live timing or source attribution were
rewritten. No new GPU, model inference, quick/full capture, comparison power or
official grade is established. The previously reviewed deployment packet remains
separate and unchanged.
