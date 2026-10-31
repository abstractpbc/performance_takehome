# Deferred, completed stock-Pi proposals

`abstract_harness.pi_artifact` supports an explicit CPU/GPU phase boundary:

1. Run the unmodified production `PiProposer.propose` on CPU with the real optimization
   program, profile, current source, actual historical reports and runtime/model inputs.
   Pi chooses its own change. A prescribed provider-compatibility fixture is separate.
2. Independently verify the official Pi distribution and normal complete exchange,
   reconcile every new provider request, stop the gateway, and seal the private artifact.
3. After fresh financial admission, start a separately bounded GPU allocation. Its
   `Harness.run` establishes a fresh baseline, consumes the verified completed proposal,
   builds, measures a fresh control and candidate, and keeps or reverts normally.
4. Revalidate the selected image, export complete evidence, and delete the allocation
   within its original deadline. New selected images still require immutable publication
   and anonymous delivery verification.

This is a split-phase optimization pipeline. It is not a claim that Pi ran inside the
GPU process, that an incomplete Pi exchange completed, or that a local gain is an
official performance score. Existing failed attempts remain separate evidence.

## Sealing

The separate [one-response no-tools overlay](PI-ONE-RESPONSE.md) supports a declared
model-authored JSON response with controller-written files. The four-tool sealer
below keeps its original actual-tool-write requirements; the two execution policies
are not interchangeable.

`seal_completed_pi_proposal(proposal, *, proposal_context, context_dir, compatibility, provenance_files,
provider_receipt_paths, journal_path, request_scope, output_dir, purpose="optimization")`
performs CPU-only verification and writes a new private artifact. It starts no model,
provider or Docker service. Its ordinary patch-application subprocess runs only during
sealing; replay starts no subprocess.

The sealer requires a normally completed production proposal, successful final assistant
response and agent completion, reconciled SDK usage, and an actual stock-tool write of
valid metadata. It preserves the returned model patch, experiment, hypothesis and notes.
A saved `prompt_sha256` must match the retained prompt bytes. The complete prompt is
then reproduced by the production renderer from the original `proposal_context`,
including the actual program, profile, history, read-only inputs and budget description.
An older proposal cannot be sealed with newer instructions. The original context and
prompt stay private; fresh GPU history and host-local paths are not compared to them.
A recovered patch must equal the actual scratch edit; an explicitly written patch must
have stock-tool provenance. The patch must apply and pass the existing single-knob
delivery guard on a separate context. The original source must remain unchanged.

For every actual request, the sealer checks the full private WAL hash chain, terminal
SSE usage, provider receipt, matching SDK terminal token counts and the original
financial Journal hash chain and settlement. Every request reserved under the supplied
CPU scope must be included; an UNKNOWN or additional unverified request blocks sealing.
The current provider-specific price check uses the independently selected GLM-5.2
undiscounted input/output rates; a changed provider/pricing contract needs a reviewed
implementation update. Settlement is an upper bound while the provider invoice is pending.

`request_scope` has exactly `field` and `value`; `value` has the actual scope `path` and
`sha256`. The selected reserve-row field must equal that object. The scope's real bytes
are checked and copied. Gateway shutdown and fresh resource admission remain the
external coordinator's responsibility; an artifact is not spending authority.

`provenance_files` must include `program`, `profile`, `benchmark`, `baseline_lock`,
`baseline_report`, `historical_runtime` and `model_runtime`. The profile may be the actual
harness-design subprofile or the supplied profile registry. Add independently verified
official Pi/Node distribution and original clock receipts to this mapping too. Raw
historical reports and host-local runtime information are preserved as evidence;
fresh measurement timestamps and host mount directories are not replay equality keys.

The artifact retains raw prompts, model events, sessions and private financial evidence.
**Keep the entire artifact outside public Git and public submission packets.** Its
SHA-256 seal is local integrity/provenance, not a provider signature. Retain the expected
manifest hash independently so an artifact cannot rewrite its own trust anchor.

## Stable replay compatibility

`compatibility_binding` takes these keyword arguments:

```python
compatibility_binding(
    context_dir=source_context,
    source_commit=reviewed_commit,
    harness_tree_sha256=reviewed_harness_hash,
    base_image_digest=locked_parent_image_digest,
    program_md=actual_program,
    profile=actual_harness_design_subprofile,
    benchmark=frozen_benchmark_bytes,
    baseline_lock=protected_lock_bytes,
    model_runtime=stable_model_contract,
)
```

`base_image_digest` is the immutable parent image selected by the protected baseline
lock and Dockerfile `FROM` (for this LLMD baseline, `sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f`).
It is not a historical wrapper build's local image/config ID. Identical Dockerfile
bytes can produce different wrapper IDs because build metadata varies; those actual
historical IDs remain report provenance, and the new GPU independently builds and
validates its fresh control. Do not normalize or relabel their original raw values.

`stable_model_contract` has exactly `engine`, `model_repository`, `model_revision`,
`model_inventory_sha256` and `model_args`. These describe the pinned model and container
arguments, not the host's mount directory. There is no broad path or timestamp
normalization. The explicit stable contract is also retained in `provenance_files` as
`model_runtime`; raw historical runtime information remains separate.

The protected context fingerprint includes all file bytes except the Git database and
refuses symlinks. It does not silently ignore caches or untracked source. Use clean
matching source checkouts, `-B` and inherited `PYTHONDONTWRITEBYTECODE=1` for both stages
and measurement workers. Check the fingerprint again after CPU imports and preparation
before renting a GPU. Fresh baseline measurement outputs belong outside the source tree.

## GPU consumption and costs

```python
proposer = VerifiedCompletedPiProposal(
    private_artifact,
    manifest_sha256=independently_retained_pin,
    expected_compatibility=current_source_and_model_binding,
    claim_path=coordinator_owned_stable_claim_path,
    consumer_id=unique_gpu_run_id,
)
```

The adapter checks the external seal and complete file inventory, expected source/model
compatibility, and actual current `ProposerContext` source/program/profile before returning
the exact patch and model-authored metadata. It is named
`verified-completed-stock-pi-deferred`, not `ScriptedProposer`. Artifacts explicitly marked
`purpose="test-only-compatibility-fixture"` can be verified read-only but cannot enter the
GPU loop. Compatibility test evidence cannot become an optimization result.

The manifest preserves `proposal.api_usd`, the original SDK estimate, unchanged. Because
that estimate may include cache discounts, the replayed `Proposal.api_usd` explicitly
carries the independently settled undiscounted provider upper into the GPU run's report.
`replay_cost_policy` records both views and states that replay makes no new charge. Start
that run's SDK API subtotal at zero; do not also seed this same carried cost into it.
The external original Journal remains authoritative. The adapter appends no reservation,
charge or settlement and estimates zero cost for any new request. Replay refusal has
known zero new-call cost so the loop can log the refusal without inventing an unknown bill.

Use one stable coordinator-owned claim path per artifact, retained outside the sealed
directory and across restarts. The claim is created exclusively and synced before the
proposal is returned. A second or uncertain consumer fails closed. Reconcile the original
consumer's state/results before any explicit new experiment; do not delete the claim to
silently repeat an uncertain launch. This local claim is not a distributed lock across
uncoordinated copies on different machines.

The supported `Proposer.exhausted()` contract defaults to false for existing sources.
This single-proposal adapter reports exhaustion after either its offer or one refusal,
so an uncapped `Harness.run` finishes without repeatedly writing error rows. Another
consumer still encounters the durable claim and receives one explicit known-zero-cost
refusal. Existing Pi and scripted proposal behavior is unchanged.

No lease is rewritten. The previous `Harness` recovery rules still log interrupted
non-promoted attempts rather than resume their build stages. A future complete
pause-after-propose implementation would need an explicit durable pending-proposal record
and separate allocation clocks; copying an expired state and clearing its deadline is
not supported by this adapter.

Focused tests exercise valid exact replay, cost carried once into the real loop,
current-input mismatches, every retained file's corruption, missing metadata, terminal
failure/length, torn WALs, incorrect scopes, financial-chain corruption and durable
consumer refusal. Synthetic HTTP evidence and replay tests are software checks, not
target-hardware measurements or genuine optimization-model results.
