# Raw-event schema — CANDIDATE-BUILT STAND-IN

The organizer has not released the fixed measurement runner or its plan/event schemas
(`docs/RELEASE_STATUS.md`). This directory defines the schema the candidate runner
emits and the candidate evaluator consumes, so the whole suite can be validated on CPU
now and re-pointed at the organizer's evidence later.

| File | Contents |
| --- | --- |
| `request.schema.json` | One object per line of `requests.jsonl`: per-request record with monotonic client timestamps (planned send, send, headers, first byte, every SSE event, end), verbatim SSE payloads, HTTP status, finish reason, full text per channel, server-reported usage (kept, **not** trusted), independent client token counts with their resolution flag, and any error. |
| `run.schema.json` | `run.json`: engine identity (model id, `baseline/lock.json` digest and image), plan identity and digest, host, GPU allocation, phase markers, startup launches with every readiness probe and generation-probe id, client event-loop-lag evidence, and the planned population per unit. |

Both carry `schema_version` (`1.0.0`). `bench/records.py` validates files against these
documents with a dependency-free subset validator (type/const/enum/required/properties/
items/minimum/minLength); unsupported keywords are ignored, which is a documented limit.

## Invariants the evaluator relies on

- Every timestamp is `time.monotonic()` seconds from the same client process. Nothing is
  back-filled: an unobserved time is `null`, and `evaluate.py` treats `null` as missing
  evidence, never as zero.
- `events[*].raw` is the SSE `data:` payload byte-for-byte (after the parser's
  `data:`/leading-space stripping), including the literal `[DONE]`. The evaluator
  re-parses these with `benchmarks.streaming` and re-tokenizes the text itself; it does
  not reuse `text`, `finish_reason`, or `client_tokens` as evidence, only as cross-checks.
- `server_usage` is informational. The independent count comes from the verbatim deltas.
- Readiness probes and generation probes are separate lists; the startup clock is
  `first_valid_generation_at - launch_mono_s`, never `readiness_at`.

## Adapter point for the organizer's real schema

`bench/records.py::load_events(directory, adapter="native")` is the only place that
touches files. To accept the organizer's runner output:

1. add `"organizer-vN"` to `ADAPTERS`;
2. implement a loader that maps its files onto `RunRecord`/`RequestRecord`, filling the
   same keys (unmapped fields become `null`, which the gates report as missing evidence);
3. if its token adapter provides per-token timestamps, emit them as events whose
   `client_tokens.resolution` is `"token"`; the fluidity path then uses token times
   directly instead of delta-arrival attribution.

Nothing in `evaluate.py` changes; `--adapter organizer-vN` selects the loader.

## Startup launch `status` is informational

`run.startup[*].status` / `first_valid_generation_at` / `first_valid_request_id` are the
runner's own summary. `evaluate.py` derives startup from the probe request records (earliest
probe that passes every gate, timed at its client `end`) and only cross-checks these fields;
a disagreement is the gate `startup_self_report_mismatch` and makes the run invalid.
