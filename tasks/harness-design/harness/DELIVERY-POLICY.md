# Optional configuration delivery policy

Generic Harness runs default to `--delivery-policy none`. A bounded LLMD pilot can
select `--delivery-policy llmd-config-v1`. The policy rejects an ineligible actual
applied scratch tree before building or measuring it; rejected attempts retain
their patch, error report and results row.

The enabled policy permits changes only to
`tasks/harness-design/Dockerfile`. Its pinned `ARG`, `FROM` and `/llmd/llmd`
entrypoint remain fixed. It accepts the reviewed tuning flags and blanking the
two existing telemetry variables. It forbids filesystem instructions, arbitrary
environment variables and changes to other context files. Each attempt must
change exactly one semantic flag or telemetry variable relative to the current
last-good source. Multiple separately kept attempts can accumulate.

Select the same mode when creating the fresh baseline and when adopting or
resuming that state for optimization:

| Stage | Required selection in the existing bounded command |
| --- | --- |
| Fresh LLMD baseline | `run --engine llmd --proposer scripted --max-attempts 0 --delivery-policy llmd-config-v1` |
| Genuine Pi optimization | `run --engine llmd --proposer pi --delivery-policy llmd-config-v1` |
| Adoption/resume/manifest preparation | Preserve `--delivery-policy llmd-config-v1` and the recorded state mode. |

These are option selections, not complete launch commands: retain the separately
reviewed model, benchmark, baseline, state paths, Pi profile and original budget
and lease bounds. State created with mode `none` cannot be resumed by this version
under the enabled policy, or vice versa; use fresh state. A direct Python caller
must set `LoopConfig(delivery_policy="llmd-config-v1", ...)` consistently.

State schema 1 records the mode, and this version checks it on resume. Older
Harness binaries ignore unknown state fields. Use exact source and runtime pins
to prevent an older binary from silently bypassing the optional policy.

This is source eligibility, not image or performance verification. Its inferred
runtime view uses sentinels for inherited public telemetry values. Final delivery
must separately verify actual image configuration and all inherited layers.
The policy does not prove flag support on a GPU, correctness, speedup, registry
publication or anonymous access, and does not replace the existing agent sandbox.

Context inspection rejects symlinks and special files, permits at most 10,000
entries and 256 MiB, and bounds the selected Dockerfile at 64 KiB. Verify the
intended context fits before provisioning paid compute.

The limited pilot conservatively rejects any comment line matching `#\s*(syntax|escape|check)\s*=` (case-insensitive) anywhere in the Dockerfile, including after ordinary comments or instructions. Ordinary comments remain allowed. This deliberately does not attempt to reproduce Docker's full parser-directive placement rules.
