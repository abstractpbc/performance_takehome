# Portable Pi provider setup

The public `PiProposer` launches stock Pi 1.0.0 with `read,bash,edit,write` and the
assignment's isolation flags. Its `--pi-bin` expects an executable, such as
`node_modules/.bin/pi`, with Node >=22.19.0 on PATH. To select an exact Node binary,
use a small executable wrapper:

```sh
#!/bin/sh
exec /opt/node/bin/node /opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js "$@"
```

The wrapper must also support `--version`. Before allocating a GPU, require a
complete **CPU-only proposal preflight**, not just an API greeting or one tool-call
fragment. `pi_protocol_smoke.py` is inactive by default. Its deterministic fixture
uses the unmodified official Pi distribution to read and edit a scratch LLMD
Dockerfile, write metadata, finish the model turn, recover the unified patch and
apply the existing one-knob source guard. It does not run Docker or a benchmark.

```sh
python tasks/harness-design/harness/pi_protocol_smoke.py --execute-fixture \
  --node /opt/node/bin/node \
  --pi-cli /opt/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js \
  --pi-package /opt/verified/pi-coding-agent-1.0.0.tgz \
  --out /tmp/new-pi-proposal-fixture
```

The archive must match the official integrity in `agent-harness/profiles.json`;
all 1243 installed distribution files are compared with it. The normal proposal
deadline is **300 seconds**, including all tool turns and the terminal response.
The fixture captures actual first and follow-up HTTP bodies. Negative modes
`--fixture-mode length`, `no-patch` and `partial` retain explicit failure statuses;
a real scratch edit with missing terminal usage is not an accepted proposal.
Fixture usage arithmetic is labelled synthetic and incurs no provider spending.

After separately approving and starting a bounded model-budget gateway, run the
same command with `--execute-live --gateway-url http://127.0.0.1:PORT/v1` in place
of `--execute-fixture`. Pass only the existing `ABSTRACT_PILOT_GATEWAY_KEY` client
capability through the environment. The upstream provider key stays in the
gateway. `--execute-live` is an execution switch, not financial authorization;
the gateway must enforce the actual shared cash limits and retain raw responses.
Require `verification.json` status `PASS` before renting a GPU. For the canonical
CLI, copy that successful **live** preflight's `agent/models.json` and
`agent/settings.json` into the new `<state-dir>/pi-agent-dir`, and select
`--pi-provider abstract-preflight --pi-model glm-5.2`. Preserve the same gateway,
client capability and wire options. Never copy the synthetic fixture provider or
reuse an expired gateway. A custom deployment launcher must use the same verified
model configuration rather than overwriting it with `reasoning: false` alone.
Live transcripts may include private model reasoning: retain the work privately.

The assigned preflight edit (`--token-batch-size=1024` to `256`) checks the
interface only. It is not an optimization result or an instruction for the real
optimization run. A passing preflight proves neither a build nor a speedup.

For DigitalOcean's GLM-5.2/vLLM route, `reasoning: false` alone did not disable
provider thinking, and `thinking.type: disabled` alone was insufficient. Pi1.0
supports the following model-level configuration without an extension or modified
distribution:

```json
{
  "reasoning": false,
  "maxTokens": 8192,
  "compat": {"maxTokensField": "max_tokens"},
  "samplingParams": {
    "thinking": {"type": "disabled"},
    "chat_template_kwargs": {"enable_thinking": false},
    "tool_choice": "auto"
  }
}
```

The official Pi1.0 `dist/core/model-config.js` accepts `samplingParams`; its
`openai-completions` provider merges those values into every request. The actual
stock-CLI fixtures assert these fields and the 8192-token wire cap on the initial
request and every follow-up. This preserves all assignment isolation flags,
including `--no-extensions`. Provider compatibility still needs the complete live
preflight: a synthetic test cannot establish the current endpoint's behavior.

Place `models.json` and `settings.json` in `<state-dir>/pi-agent-dir` before launch.
For a custom OpenAI-compatible provider, use its documented Pi 1.0 model schema,
an environment-variable `apiKey` reference, and explicit positive input/output/
cache prices in USD per million tokens. Forward only that named client key with
`--api-key-env`; a gateway's upstream provider secret must stay outside Pi.

In the genuine 28 October external proposal run, the successful continuation set
Pi's `maxTokens: 16384`, the outer gateway configuration to 16384, and
`contextWindow: 65536`. A later source audit found that its inner request validator
still clamped output to 8192; the client/configuration values do not establish a
16384 wire limit. An earlier 4096 outer ceiling ended with `stopReason=length`
before files were written. These were deployment settings; the canonical proposer
does not impose 4096. Verify the actual outgoing cap, leave room for tools and
context, and reserve their full possible cost. A larger output ceiling does not
guarantee a usable answer. The documented provider may have different current limits.

The bounded run used this task-local `settings.json`:

```json
{
  "retry": {"enabled": false, "maxRetries": 0, "provider": {"maxRetries": 0}},
  "compaction": {"enabled": false},
  "cacheWarming": "off"
}
```

`PI_OFFLINE=1` freezes Pi startup package/catalog work while permitting requests to
the configured provider. It does not make model requests free or disable all provider
behavior. An external gateway must enforce a shared aggregate budget across all
model/tool turns and retain complete raw responses, terminal usage and failures.
`--provider-response-dir` records the retention location; the harness does not
verify those external bytes. Export them with the complete attempt tree.

The harness refuses output-limit partial responses before recovering artifacts,
while preserving their known charge. Missing terminal usage, HTTP failures with
unknown billing, timeouts and incomplete responses can leave accounting unresolved;
the harness preserves the known subtotal and stops. A 529 response does not prove
zero billing. Do not retry or clear such a hold without separate reconciliation or
an explicit reviewed policy that continues covering its full liability.

For future canonical LLMD runs, explicitly select
`--development-benchmark tasks/harness-design/harness/development-benchmark.llmd.json` and
`--delivery-policy llmd-config-v1`; the generic CLI benchmark default differs.
Pi edits `tasks/harness-design/Dockerfile` in its scratch context, one knob only,
and writes proposal metadata. It should write files early and keep reasoning
concise. If `proposal.patch` is absent, the controller derives a unified diff from
the actual scratch edits. It then applies the ordinary guard/build/control/
measurement/keep-or-revert process; Pi does not choose the keep decision.

The actual external 28 October runner instead requested `candidate.Dockerfile` and
`proposal.json`. Preserve those raw files and disclose a deterministic diff from
the unchanged baseline Dockerfile to that exact candidate before applying normal
validation. Adding `candidate.Dockerfile` to the canonical scratch tree is not the
same as replacing its Dockerfile. An external raw-file turn must not be described
as an execution of the canonical unattended loop. Historical failed/partial turns,
their source identities and clocks remain part of the evidence.
