# Event format v1

This is our own format, because the organizer's schema doesn't exist yet. `events.py` will be the
only module that reads it, so a released schema changes that one file.

## Layout

```text
<run>/
  manifest.json      identity and planned population
  phases.jsonl       what ran when, for cost and warmup evidence
  launches.jsonl     one record per launch event
  requests.jsonl     one record per offered request
  sse.jsonl          raw stream events
  metrics.jsonl      raw /metrics scrapes (optional, diagnostic only)
  tokens.jsonl       derived token arrivals (added after capture)
  tokens.meta.json   how tokens.jsonl was derived
```

Files are UTF-8. `.jsonl` files hold one JSON object per line. Raw files are never rewritten.

**Clock:** every `t` field is seconds on one monotonic clock, starting at 0 at run start. Only
`manifest.json` holds wall-clock time.

## `manifest.json`

```json
{
  "schema_version": 1,
  "started_at_utc": "2026-11-02T14:00:00Z",
  "capture": { "tool": "capture.py", "stand_in": true },
  "suite_commit": "<40 hex>",
  "plan_sha256": "<64 hex>",
  "mode": "quick",
  "seed": 20261030,
  "image_digest": "sha256:<64 hex>",
  "model": { "repository": "Qwen/Qwen3.6-27B", "revision": "<40 hex>" },
  "hardware": { "gpu": "AMD Instinct MI355X", "gpus": 1 },
  "filesystem_cache": "warm_fs",
  "planned_requests": { "chat_light": { "0": 60, "1": 58 } },
  "planned_launches": 3,
  "aborted": null
}
```

- `planned_requests` maps cell, then repetition index, to the count of measured requests. Warmup and
  startup probes aren't included.
- `aborted` is `null`, or a string giving the reason capture stopped early. An early stop writes
  `early_stop:<reason code>`.

## `phases.jsonl`

```json
{"phase": "warmup", "launch": 0, "t_start": 12.0, "t_end": 40.5, "status": "ok"}
```

- `phase`: `launch`, `warmup`, `measure`, `drain`, `cleanup`.
- `measure` and `drain` also carry `cell` and `repetition`.
- `status`: `ok`, `timeout`, `aborted`.
- A `warmup` record is the evidence that warmup finished. These records supply the measured cost
  breakdown.

## `launches.jsonl`

```json
{"launch": 0, "event": "launch_start", "t": 0.0}
{"launch": 0, "event": "readiness_ok", "t": 31.2, "model_id": "..."}
{"launch": 0, "event": "probe", "t": 32.0, "request_id": "p-0-0"}
{"launch": 0, "event": "launch_stop", "t": 900.1, "exit_code": 0}
```

- `event`: `launch_start`, `readiness_ok`, `probe`, `crash`, `startup_timeout`, `launch_stop`.
- Only the first `readiness_ok` needs recording.
- Each `probe` has a matching `requests.jsonl` record with `phase=probe`.
- Validity isn't stored here. `evaluate.py` finds the first valid completion from probe evidence.

## `requests.jsonl`

```json
{
  "request_id": "r-chat_light-0-17",
  "phase": "measure",
  "launch": 0,
  "cell": "chat_light",
  "repetition": 0,
  "prompt_id": "short-0042",
  "prompt_sha256": "<64 hex>",
  "is_canary": false,
  "is_anchor": false,
  "max_tokens": 256,
  "min_output_tokens": 32,
  "ignore_eos": false,
  "prefix_cache": "miss",
  "t_scheduled": 14.20,
  "t_sent": 14.21,
  "t_headers": 14.25,
  "t_end": 19.30,
  "http_status": 200,
  "content_type": "text/event-stream",
  "client_outcome": "closed",
  "error": null
}
```

- `phase`: `warmup`, `measure`, `probe`.
- `client_outcome`: `closed`, `reset`, `timeout`, `aborted`. This is what the client saw.
- `aborted` means capture stopped the request, so it can't be charged to the server.
- `t_sent` to `t_end` and `http_status` may be `null` if the request never got that far.

## `sse.jsonl`

```json
{"request_id": "r-chat_light-0-17", "seq": 0, "t": 14.61, "data": "{\"choices\":[...]}"}
{"request_id": "r-chat_light-0-17", "seq": 41, "t": 19.29, "data": "[DONE]"}
```

- `data` is the **raw** SSE payload string, unparsed. A malformed payload is still recorded.
- `seq` counts up from 0 within a request.
- Text, finish reason, usage, model ID and `[DONE]` are all read from here.

## `metrics.jsonl`

```json
{"t": 15.0, "status": 200, "body": "requests_waiting 3\n..."}
```

- `body` is the raw `/metrics` text, polled during the run.
- These are server claims, so they're for diagnosis only and never affect a state or metric.
- A missing file or failed scrape has no effect on the run.

## `tokens.jsonl`

```json
{"request_id": "r-chat_light-0-17", "index": 0, "t": 14.61, "channel": "content", "token_id": 1234}
```

- Derived from `sse.jsonl`: retokenize cumulative text per channel, and give each token the receive
  time of the event that completed it.
- Tokens from one event share a timestamp, so bursts stay visible.
- `index` orders tokens across both channels by arrival.
- A token count is independent of the server's `usage`.

## `tokens.meta.json`

```json
{"tokenizer": "Qwen/Qwen3.6-27B", "revision": "<40 hex>", "tool": "tokenize_events.py"}
```

## Prompt and reference files

These belong to the plan, not to a run.

Prompt set, one object per line:

```json
{"prompt_id": "short-0042", "messages": [{"role": "user", "content": "..."}]}
```

Canary prompts also carry `"expected": "..."`, the required answer after trimming whitespace.

Canary prompts are generated from the plan seed. A canary is only used if the baseline answers it
correctly during reference capture, so a weaker model simply keeps fewer canaries.

`prefix_cache=miss` prepends a unique prefix derived from seed, cell, repetition and request index.
The derivation is deterministic, so two runs of one plan send identical bytes. Anchors and canaries
get no such prefix.

Reference file, one object per line:

```json
{
  "model_revision": "<40 hex>",
  "prompt_sha256": "<64 hex>",
  "output": "The migration happens in three steps...",
  "output_sha256": "<64 hex>",
  "tokens": 128,
  "finish_reason": "length",
  "stable": true
}
```

`output_sha256` hashes the UTF-8 text of `reasoning_content` followed by `content`.

The reference holds anchor prompts only, and nothing in it is hand-written. A reference-capture
command generates it from the running baseline, one request at a time, and repeats every anchor
`anchor.passes` times. `stable` is true when all passes matched. Changing the model means rerunning
that command.
