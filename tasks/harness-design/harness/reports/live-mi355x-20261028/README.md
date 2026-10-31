# 28 October Task 2 public evidence

This packet preserves four genuine stock Pi proposal intents and two explicitly
attributed controller neighbors. The final mixed-workload selection is batch size4,
with a passing fresh final30 after selection. Its local serial comparison improved
TTFT1.0778 and FTGR1.2654; supplemental checks are descriptive and no official or
unseen-workload grade is claimed. Actual publication, complete anonymous graph and
separate anonymous Docker pull/inspect/CPU-help proof all passed; see
[`publication/VERIFIED.json`](publication/VERIFIED.json).

The later [measured Pi2 feedback cycle](pi2-measured/README.md) adds 90 actual
control/candidate/final requests, all passing, with a no_change decision. The
baseline remained selected after that cycle. The first cycle and its failed candidate are preserved.

The current measured selected immutable reference is
`ghcr.io/sharonbasovich/performance_takehome-task2-llmd@sha256:84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb`.
Its complete [controller selection and overlay](controller-neighbors/README.md)
retains the frozen rule, both actual neighbors, supplemental probes and post-choice
final30. Across all stages there are570 primary measured requests plus100
supplemental requests. The earlier baseline7db remains separately verified history.
The first cycle's rejected native-drafter candidate is platform manifest
`sha256:ab62d880b43412b895dbc1abc25a7eb092f954df40b0f72c896e5ea5bbbee5dd`
with config `sha256:ed53dd105fa9bc76c1db8ad8261f8cfc435de4e03684b08931025261ff5670de`.
The two identities are kept separate; the candidate was not published as a winner.

## Files and attribution

- `baseline/`, `control/`, `selected-final/`: exact full reports, samples, result
  receipts, and 31-line durable trace files (one startup probe plus 30 measured
  requests). `baseline/golden.json` holds six outputs established by the baseline.
- `candidate/`: startup-failure receipt, container logs, and an empty request trace
  file. There is deliberately no candidate performance report.
- `pi/`: exact proposal, Dockerfile and exit receipt; sanitized runtime inventory
  and tool-event metadata. Only event names, call IDs, sequence numbers,
  restrictive work-filename metadata, argument byte counts/hashes, stop reasons,
  and selected runtime/artifact hashes are retained from the private transcript.
- `candidate-build/`, `candidate-metadata/`: actual guard result, build arguments,
  Docker image inspection, build logs, and exact small OCI manifest/config bytes.
  Config includes inherited public-image telemetry environment entries; it does
  not contain a human's provider or registry credentials.
- `actual-source/`: exact canonical source at
  `99c006dfeb1fae2c2e33f3c066927ee43bf056ec`, including the frozen benchmark.
- `actual-orchestration/`: the exact external build, metadata export, and
  control/candidate/final helpers. `.py.txt` preserves source bytes without
  importing the historical scripts as current package code.
- `paired-result.json`, `reviewed-summary.json`: actual phase results and the
  reviewed outer-bundle identity. The outer bundle was 76,372 bytes, SHA-256
  `f3a564f3df1e20129eb2f888087fc2e382b83e6d4db775540310c5d86f3404c4`.
- `later-unmeasured-pi2/`: a later page-chunk-size proposal and completion receipt,
  preserved as its original unmeasured snapshot. Its later actual measurements
  live separately in `pi2-measured/`; they do not rewrite this completion receipt.

The external helpers call canonical `configuration_delivery`, `measure`, and
`decision` functions. They do not invoke the canonical unattended harness loop.
The retained source shows the real isolation, mounted model, internal Docker
network, trace fsync, exact-owner cleanup, and fresh-final selection path. No GPU
or model API action is run by reading or validating this packet.

The Pi1 transcript metadata covers its successful continuation only. Earlier
HTTP 529 and output-length sessions remain distinct failed history. Pi1's
configured/requested 16,384 output tokens were capped to 8,192 on the historical
wire. The later Pi2 gateway's effective 16,384 cap must not be backdated to Pi1.

## Offline reproduction

From a normal Git checkout, use the repository's cached/locked development
dependencies and run:

```text
python -B -m unittest discover -s tasks/harness-design/harness/tests -t tasks/harness-design/harness -v
python -B -m tools.check_integrity --base 81aead87d78ed468c4c374fb42921471b44c3f80
```

`tests.test_current_delivery_manifest` recomputes every inventoried SHA-256/size,
checks the platform/config identities, verifies all three 30-request reports and
their six correctness gates, checks five repetitions per prompt against the
golden outputs, and verifies that the failed candidate has zero traces and no
invented report. Historical provenance assertions continue to use the preserved
26 October manifest with their original expectations.

`inventory.json` excludes itself to avoid circular hashes. It binds the raw
evidence, actual source and selected derived metadata; explanatory documents
are included only where explicitly inventoried.
Verbatim raw evidence is marked `-text` in the task's `.gitattributes`; line-ending
normalization must not change its hashes. The historical baseline is already public:
[PR 5](https://github.com/sharonbasovich/performance_takehome/pull/5)
contains its complete anonymous-graph and Docker-pull/help verification. New84d's
separate actual [hosted run](https://github.com/sharonbasovich/performance_takehome/actions/runs/40794303749)
and receipts are retained under `publication/`. The hosted checker streamed and
hashed all25 complete objects; bodies were not retained/rehashed locally. This packet
does not download the 6 GB image or include any model weights.

Re-running GPU measurements or paid Pi proposals is a separate authorized action.
The historical external scripts contain an expired hard stop and host-specific
paths; they are reproducibility evidence, not a current deployment command.
For a future canonical unattended run use the current `harness/run.py`, the
correct `--development-benchmark` option, the identical LLMD profile for all
phases, fresh evidence paths, and the current [provider instructions](../../PI-PROVIDER.md).
Never overwrite the historical main `results.tsv` or treat software tests as a
new GPU result.

## Four-phase TSV schema

`results.tsv` uses tab-separated fields:

`phase, status, image_digest, measured_requests, gates_passed, startup_s, elapsed_s, outcome, kept`.

The baseline, control and selected-final rows each record 30 measured requests.
The candidate row records zero, `PARTIAL_FAILURE`, blank gate/timing values,
`gate_failed`, and `kept=False`. This TSV is a compact index into the raw files;
it is not the canonical loop's experiment ledger or a performance comparison.
The original main 14-row CPU ledger remains unchanged and its old FTGR/keep
decisions remain invalid performance evidence.

## Pi3 follow-up

The genuine [Pi3 prefill-chunk proposal](pi3-measured/README.md) also completed
90 actual requests with all six gates and returned `no_change`. The selected
baseline passed another fresh final30. The three retained cycles contain270
measured requests in total. All input traces report14–27 tokens, below the
original256 prefill threshold; this contradicts the proposal's possible
two-chunk hypothesis. Its original proposal remains retained as written.
The Pi3 packet includes original built Dockerfile, exact build and selected
metadata, full reports/samples/traces, actual source and metadata-only Pi events.
The unchanged raw first/Pi2 packets and prior manifest snapshots remain available.
Pi4's [raw rejection, controller pin repair, serial improvement and burst regression](pi4-measured/README.md)
are retained separately. It seeded the family but did not choose the controller's
4/8 values. The final batch4 selection and post-choice30 live under
`controller-neighbors/`; no canonical unattended execution is inferred.
