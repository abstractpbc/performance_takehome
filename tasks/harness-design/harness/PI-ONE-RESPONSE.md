# One-response, no-tools Pi execution overlay

`abstract_harness.pi_one_response` offers a declared alternative execution policy
for a genuine optimization proposal. It runs the unchanged official Pi1.0.0 binary
with `--offline --no-tools`, the existing isolation flags and the supported model
configuration. The provided four-tool profile remains an unchanged **baseline
reference**; this overlay activates no tools. The assignment permits candidates to
improve orchestration, prompts and tools.

The installed official CLI help documents that `--no-tools` disables built-in and
extension tools, while `--offline` disables startup network operations. Offline
does not replace the explicit separately authorized coding-model gateway.

## Proposal contract

```python
proposer = OneResponsePiProposer(
    PiLaunchConfig(..., timeout_s=300),
    profile=unchanged_harness_design_profile,
    node=verified_node_path,  # optional when pi_bin is an already pinned shim
)
proposal = proposer.propose(actual_context)
```

Use `one_response_model_config` to set the official SDK's model output cap to2048,
`tool_choice="none"`, and both supported thinking-disabled options. Do not reuse an
`auto` tool choice with no active tools. The provider gateway must independently
enforce one forwarded request ever under this new scope, input/full-reservation
bounds, output2048, no tool definitions/history and a60-second upstream response
deadline. Pi has its ordinary300-second overall completion deadline, leaving room
for the provider's timeout and cleanup. An effective overall window below75seconds
is refused before any process starts. No scope, spending or provider access is
authorized by these helpers.

The rendered evidence contains the exact current Dockerfile, selected workload and
actual baseline/runtime inputs, attempt history, original playbook text and its hash,
and the unchanged profile as reference. Controller instructions explicitly replace
the playbook's tool/file-operation and output-format steps. Source, evidence,
workload and correctness protections still apply. The model chooses its own single
knob and hypothesis; no winning value or prepared patch is supplied by the helper.
The complete prompt is bounded to64KiB before launch.

The sole final response must be a JSON object with exactly four nonempty string fields:

- `experiment`: short slug;
- `hypothesis`: intended metric, mechanism and risk;
- `notes`: uncertainty and qualifications;
- `patch`: complete unified diff against the exact original Dockerfile.

Markdown fences, duplicate/extra keys, incomplete JSON, tool calls, additional SDK
assistant turns, length/error endings and uncompleted usage are rejected. The class
will not launch a second model call from the same instance. The gateway's durable
one-request limit prevents retries across uncertain process restarts.

The **controller**, rather than Pi's stock `write` tool, parses the actual final text
and writes `proposal.patch` and `proposal.json`. Their model-authored field values
remain exact. It also retains the unmodified final response, raw SDK events/sessions,
prompt, policy and process status. No syntax, base pin, knob, hypothesis or patch is
manually repaired. A parseable response is not yet an eligible build or a speedup.

## Sealing and later GPU use

`seal_one_response_pi_proposal` has the same keyword arguments as
`seal_completed_pi_proposal`, including the original `proposal_context`, compatibility
binding, private provenance, exact new request scope, receipts and original Journal.
It is a separate verifier: the original four-tool sealer still requires an actual
successful stock metadata write and its original prompt/completion checks.

The one-response verifier requires exactly one normally completed SDK assistant
response, final text only, and no tool events. It checks that:

- the original retained prompt/hash equals the deterministic overlay renderer;
- provider SSE text equals the actual SDK final text byte for byte after JSON decoding;
- all returned patch/metadata values equal that strict model JSON, and controller files
  contain those exact values;
- exactly one provider receipt has output cap2048/response60, complete hash-chained WAL,
  terminal usage, matching SDK tokens and a matching original-Journal settlement;
- the exact model patch applies unchanged and passes the existing single-knob guard,
  while the source context remains unchanged.

The immutable artifact retains schema `completed-stock-pi-artifact-v1` and adds an
explicit `execution_policy` with mode `one-response-no-tools-v1`, empty active tools,
one remote call, output2048/provider60, stockPi1.0.0, final-text-JSON model authorship
and controller-written files. It does not pretend that four tools ran. The original
SDK estimate remains separate from the provider-settled undiscounted upper.

The existing verified GPU adapter consumes a valid optimization artifact without any
model/provider call and labels its proposer
`verified-completed-stock-pi-one-response-no-tools-deferred`. It carries the settled
upper once, then exhausts. The original fresh baseline, build, control/candidate
measurements, gates, keep/revert, selected-final validation, immutable image delivery
and absolute cleanup obligations remain unchanged. Compatibility keeps the baseline
profile binding; the execution overlay is independently sealed. The locked parent
`FROM` digest is the stable base identity, not a historical wrapper build ID.

All raw model and financial evidence remains private. Test-only compatibility
artifacts cannot enter the GPU loop. Deterministic loopback tests use the actual
official CLI and capture its outgoing payload, but they perform no inference and
establish no optimization result. Failed prior research/tool runs remain intact;
this overlay does not turn them into successful proposals or official grades.

## Completed real execution

The [30 October evidence packet](reports/live-mi355x-20261030/README.md) records a genuine one-response CPU proposal, strict unchanged-patch seal and later canonical GPU baseline/build/pair/KEEP plus fresh final30. The provider gateway additionally requested JSON-object response format. Prior malformed-patch and prose responses remained failed. This completed execution is distinct from the loopback protocol tests above, and its serial FTGR result is local development evidence rather than an official grade. The separately recommended public batch4 image retains its own historical controller-selection attribution.
