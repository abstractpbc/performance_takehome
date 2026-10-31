# Mock engine — CANDIDATE-BUILT STAND-IN

A CPU-only, stdlib-`asyncio` imitation of an OpenAI-compatible streaming server. It exists
so the runner, schema, evaluator and tests can be exercised without a GPU or the organizer's
engine. Its timings are **injected**, not measured; nothing here says anything about llm-d.

```
cd tasks/benchmark-design/suite
python -m mock_server --profile stall --seed 7 --port 8089 [--time-scale 0.5]
```

The server prints `{"base_url": ..., "profile": ..., "seed": ...}` once bound. `--port 0`
picks a free port. All randomness derives from `sha256(seed | X-Bench-Request-Id)`, so the
same seed and request ids reproduce byte-identical streams.

## Fault families → profiles (`faults.py::PROFILES`)

| `suite/README.md` family | Profile(s) | What is injected |
| --- | --- | --- |
| clean control | `clean` | steady 12 ms decode, 5% jitter |
| noisy control | `noisy` | 60% timing jitter, no structural faults |
| bursts | `burst` | 8 tokens per SSE chunk after an 8x wait |
| stalls | `stall`, `slow_decode` | two 1.5 s mid-stream stalls / 4x slower decode |
| late first tokens | `late_first_token` | +2.0 s before the first token |
| reasoning channels | `reasoning_channel` | 24 `reasoning_content` deltas before the answer |
| Unicode / token-boundary splits | `unicode_split` | CJK, ZWJ emoji, combining marks, deltas split mid-grapheme and mid-word |
| missing completion | `missing_done`, `missing_finish_reason` | no `[DONE]` / `finish_reason` null |
| errors and drops | `http_error`, `drop` | 30% HTTP 500 / 30% connection closed mid-stream |
| invalid output | `wrong_text`, `garbled_text`, `truncated_text` | altered codes, U+FFFD and mojibake, 40% of text with `finish_reason=stop` |
| false usage | `false_usage` | `usage.completion_tokens` = 3x truth |
| misleading readiness | `misleading_readiness` | `/v1/models` 200 at once, inference 503 for 3 s |
| slow startup | `slow_startup` | `/v1/models` 503 for 2 s, inference ready at 2.5 s |

Two further families cannot be produced by a server and are injected into recorded
events by `fixtures/generate.py`: **invalid timing** (non-monotonic / missing timestamps)
and **client lag** (event-loop lag evidence in `run.json`).

## Token-stream oracle

`GET /_mock/oracle` returns, per request id: the true generated text, the engine's own
token count (its tokenizer, `mock_tokenize`, deliberately differs from `bench/tokens.py`),
the true per-token emission offsets, the usage it *claimed*, whether `[DONE]` was sent,
whether the stream was dropped, and whether the answer is correct. Fixtures store this as
`oracle.json` next to the events; `evaluate.py` never opens it (see
`tests/test_fixture_independence.py`).
