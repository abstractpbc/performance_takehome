# Exact planned startup cohort

Incoming evidence with two planned cold starts could previously replace a failed
launch with an extra successful retry, or include other unplanned launches, while
still yielding a valid report. The evaluator now requires both recorded launches
and gate-valid cold generations to match the exact planned population. Extra
launches make the run inconclusive. All observed timings and failures remain in
the diagnostic report; retries do not redefine the cohort.

Three new regressions cover a retry replacing a failed cold launch, an extra cold
success, and an extra warm attach. The complete software suite passed 199 of 200
tests; the original unavailable opaque held-out-key test remains skipped. Eighteen
focused tests, changed-Python Ruff and protected-file integrity passed.

The new proof records canonical LF receipt digests and tested source hashes for
the bc22 archive plus three overlays. The previous submission digest mistakenly
identified the working CRLF verification file; its corrected Git LF digest is
recorded separately without changing the old receipt or test log.

CPU software checks do not establish new GPU measurements, quick/full completion,
comparison power, or an official grade. Historical calibration and the separate
frozen deployment packet are unchanged.
