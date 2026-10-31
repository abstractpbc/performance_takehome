# Greptile review follow-up

The first Greptile review inspected `f6daae7292db6380221fdbea7c917790e1c33d13`.
All six findings were confirmed; they concern the candidate runner/evaluator, not
measured target GPU performance. `suite/tests/test_greptile_review.py` exercises them.

| Finding | Correction |
| --- | --- |
| A second cold launch can probe the preceding warm server. | Stop and reap the preceding owned foreground process before a repetition; refuse a pre-existing HTTP responder; check child liveness before and after generation probes. POSIX engines run in an owned session for termination. |
| Nonces repeat across load points. | Seed prompt identity with the complete scheduled unit ID, including load and repetition, while retaining deterministic reruns. |
| A null planned send time bypasses the send-lag gate. | Measurement records require a finite planned send time. Startup and warmup can still have no planned send. |
| FTGR comparisons silently drop unresolved traces. | Carry unresolved decode counts into comparison samples and withhold the decode comparison whenever either population has one. Other metrics retain their own decisions. |
| An oversized SSE event aborts capture. | Convert shared-parser exceptions at event boundaries or EOF into a protocol-failed request while retaining preceding complete events; subsequent requests can run. |
| CJK whitespace moves token arrival times earlier. | Derive regex token end positions from original match/character offsets rather than the lengths of pieces after whitespace removal. Mock counts are unchanged. |

The cold-start command must run the actual foreground engine on the supplied endpoint.
A pre-existing responder is an aborted launch rather than a cold-start measurement.
`--stop-after-launch` controls final teardown; intermediate repetitions always stop the
previous engine. Docker or remote launch wrappers must forward termination to their
actual serving process and must not detach it.

Historical mock captures retain their original source identity. These fixes do not
relabel or regenerate them, and do not establish LLMD/MI355X performance or parity.
The public Unicode discrimination row was recomputed from those unchanged raw
captures using `build_validation.discrimination_rows`: its displayed strict-fluidity
signal changes from 0.227 to 0.222 after the corrected character offsets. Its checks
and outcome are unchanged. Other committed per-fixture reports retain their historical
analysis source rather than being silently represented as fresh captures.

The existing misleading-readiness integration fixture now explicitly sets its
retry interval to two seconds and scales it to 0.4 seconds. Its unchanged `>0.2s` clock assertion
therefore follows the intended retry schedule instead of relying on incidental
host overhead; the mock plan's shorter 0.3-second interval could fail after scaling
on a fast host.

Validation at `b1987ff58c8511bcedb64ce426502480fd2958d4`: 131 native Linux tests run, 130 passed and 1 opaque-key skip, in 90.683 seconds. Both pinned tokenizer snapshots were available. Locked Ruff 0.16.10 and the protected-file integrity check passed. The seven focused regressions also passed on Windows. See `greptile-tests.json` for the source/tree receipt.
