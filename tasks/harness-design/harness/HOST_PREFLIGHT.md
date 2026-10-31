# Prepare and inspect a future MI355X host

`host_preflight.py` is a standard-library, read-only inspection command. It installs
nothing, pulls no image, downloads no weights, starts no container, changes no GPU/host
setting, and makes no provider/registry call. It uses bounded local version/inventory
commands and temporary inspection files. It writes structured JSON to stdout and exits
zero only when every requested local check passes. `FAIL` means an observed mismatch;
`MISSING` means a prerequisite or adequate evidence is absent. Each check gives its next
action. An inspection PASS is not proof of inference correctness or performance.

Run from a clean Linux/amd64 checkout. It needs no provider credential:

```sh
python3 -B tasks/harness-design/harness/host_preflight.py \
  --model target --model-dir /srv/abstract-target --hash-model \
  --render-device /dev/dri/renderD128 --disk-path /srv \
  --node /opt/abstract/node/bin/node --node-sha256 TRUSTED_NODE_BINARY_SHA256 \
  --pi-dir /opt/abstract/pi/node_modules/@earendil-works/pi-coding-agent \
  --pi-tarball /opt/abstract/artifacts/pi-coding-agent-1.0.0.tgz \
  --registry registry.example/approved-team/abstract-llmd \
  > /var/tmp/abstract-host-preflight.json
```

Omit unavailable inputs for an initial gap report. Use `--model smoke` and the separate
smoke snapshot directory for its preflight. `--hash-model` streams every published file
and can take minutes for a 55-GB snapshot; without it, the model check stays MISSING
even if sizes and structures match. The default manifests in `model-pins/` contain public
exact-revision Hugging Face file inventories: LFS SHA-256 for large files and Git blob
SHA-1 (including Git's blob prefix) for ordinary repository files. Their repository and
revision must match the protected baseline. No model code is imported or executed.

The target inventory has 29 files / 55,586,107,940 bytes; smoke has 15 files /
16,397,461,266 bytes. The pinned marker is setup bookkeeping, not proof of those bytes.
The checker verifies config/tokenizer presence, materialized files, indexed shard
completeness, safetensors header/data extent consistency, all expected sizes and full
hashes when requested. It rejects symlinks and incomplete files. Model inventories were
retrieved from the public exact-revision API on 2026-10-25; source URLs are in each file.

GPU checks correlate SMI model identity with the selected render device's PCI BDF and
sysfs VRAM, and require accessible `/dev/kfd`, exactly one visible AMD render device,
MI355X identity, at least 256 GiB VRAM, and a `gfx950` ROCm agent. Supported read-only
inventory paths are `rocm-smi --showproductname --showbus --showmeminfo vram --json` or
`amd-smi static --asic --bus --vram --json`, plus `rocminfo`. Unrecognized SMI JSON stays
MISSING; it cannot certify hardware from a device node alone. See AMD's
[SMI CLI documentation](https://rocm.docs.amd.com/projects/amdsmi/en/latest/how-to/amdsmi-cli-tool.html).

Capacity checks use process affinity/cgroup limits and plan for 16 serving cores/128 GiB
plus 8 client cores/16 GiB. A separately approved client arrangement needs a documented
plan rather than claiming these combined local checks passed. Default free storage is
180 GiB on `--disk-path`, plus 80 GiB on Docker's filesystem; these are conservative
candidate planning thresholds, not organizer entitlements. `--min-free-gib` adjusts the
former explicitly. Host `/dev/shm` is reported, while the required 16-GiB container shm
is a separate launch setting. Neither a capacity pass nor device visibility establishes
exclusive tenancy or an approved lease.

Docker must be the local Unix default context: endpoint overrides are refused so local
GPU observations cannot accidentally certify a remote daemon. The pinned image is only
inspected if already cached. Buildx availability is checked without starting a builder.
Registry checks inspect credential configuration presence only, never print credential
values or call a helper. An empty auth entry does not count. Registry push scope and
organizer accessibility remain publication checks.

Pi's supplied tarball must match the protected npm SHA-512 integrity. Every installed
top-level package file must match that verified tarball before its isolated `--version`
is executed. Node must match the trusted binary SHA-256 supplied from a separately
verified official archive and report >=22.19.0. This does not freeze Pi transitive
dependencies; preserve the audited npm lock/install and rerun `pi_protocol_smoke.py`
before using a live provider. Version commands receive no provider credentials and use
offline startup plus a fresh temporary Pi agent directory.

## Copy preparation

Copy only reviewed source and verified runtime artifacts; the preflight does not copy
personal Docker configuration, provider keys, browser state or credentials. Preserve
the isolated Pi installation's dependencies and lock alongside its top-level tarball.
For example, after creating the current source bundle and runtime archive locally:

```sh
sha256sum source.bundle node-runtime.tar.xz pi-runtime.tar.gz pi-coding-agent-1.0.0.tgz > handoff.sha256
scp source.bundle node-runtime.tar.xz pi-runtime.tar.gz pi-coding-agent-1.0.0.tgz handoff.sha256 HOST:/srv/abstract-handoff/
# On the host: verify before unpacking or running anything.
cd /srv/abstract-handoff
sha256sum -c handoff.sha256
git bundle verify source.bundle
git -c core.autocrlf=false clone -b codex/abstract-harness-audit source.bundle /srv/abstract-harness
```

`HOST` and filenames are placeholders, not a provisioning instruction. Transfer trusted
checksums through the approved channel. Review archive paths before extraction. Set up
the pinned snapshots separately using the protected preparation tool only when storage
and allocation are ready; this preflight never triggers those downloads itself.

Optional ABI help is a **separate explicit container run**, with no model/GPU/egress:

```sh
docker run --rm --network none --cpus 2 --memory 1g \
  --entrypoint /llmd/llmd PINNED_IMAGE --help
```

Use a bounded outer timeout and the exact protected image reference. This creates a
temporary container and is intentionally absent from the read-only preflight. The real
GPU smoke, live Pi/provider call, enforced lease/spend limits and final registry digest
remain subsequent steps.
