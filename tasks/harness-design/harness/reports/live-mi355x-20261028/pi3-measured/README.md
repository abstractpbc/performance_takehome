# Pi3 measured feedback: prefill chunk 256 to 1024, no change

The genuine stock Pi proposal changed only `--prefill-chunk-size=256` to `1024`.
The actual GPU candidate was
`sha256:7a555bea9efaca1b92e5f9e1c86bfe19e95a63c434161d977febd3fc5d71961a`.
Control, candidate and fresh selected-final each completed 30 requests and passed
all six development correctness gates. Each phase retains its complete report,
samples, 31 durable records including startup, result and container logs.

The canonical prompt-stratified decision was **no_change**, `kept=false`:

| Metric | Speedup | 90% bootstrap interval | Decision |
|---|---:|---|---|
| TTFT | 1.0191234066 | [0.9984306497, 1.0370541231] | no_change |
| FTGR | 1.0103760643 | [1.0026015830, 1.0157667794] | no_change |

The FTGR interval is above one, but the 1.04% effect is below the required 3%.
One startup was observed per side: 24.387 s control and 32.063 s candidate.
That passes the startup ratio gate; one sample does not support a startup
confidence interval. The selected baseline's fresh final startup was 30.475 s.

All retained traces report only 14–27 input tokens, including the actual chat
template: startup14, colors15, capital23, sum27, haiku19, explain22 and list22.
These fit the original 256-token prefill chunk, so this proposed threshold
change did not bind the actual inputs. The original proposal is retained as
written, including its subsequently disproved input-length hypothesis.

The selection remains the already public baseline:
`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:7db01160c57479b9d608b7c2e6fc4e6b5d438e53eb4d44dc9f3ab0441d1bb20d`.
Its exact selected config/platform metadata is retained. No new publication,
optimization gain or official grade is claimed.

The real cycle used external stage orchestration with canonical guard, measure
and decision source at `419a0bd07c598dcbaea80b757e74c938c75d8599`; it was not
execution of the canonical unattended Harness loop. It finished at 23:31:24Z,
within the unchanged original GPU deadline. The offhost archive SHA-256 is
`63f0a0882fc1096780a870b5bd4b3f68f7efdc66c3512a21b35f439e5e8d4562`.
The public packet includes exact original Dockerfile/build evidence and actual
source. The three-row `results.tsv` follows the parent packet's declared schema
and preserves the earlier historical results separately.
