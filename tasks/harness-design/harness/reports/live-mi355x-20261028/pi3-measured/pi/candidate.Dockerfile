# Candidate LLMD image for Task 2 (harness-design).
#
# Thin wrapper over the organizer's pinned LLMD image (baseline/lock.json). The
# upstream ENTRYPOINT (/llmd/llmd) is preserved; the default CMD keeps the
# contract argument `--io-impl=zio` and spells out the pinned binary's documented
# defaults (verified with `/llmd/llmd --help` on CPU, see NOTES.md). The runner
# appends `--model=/models/model`. The pinned image ships no shell, so this
# wrapper has no RUN steps: the CMD line (and the ENV lines) ARE the experiment
# surface. One experiment = one flag change in a patch, and
# `docker image inspect --format '{{.Config.Cmd}}'` shows the exact runtime
# flags of any digest.
#
# Build (CPU-only host is fine; no GPU is needed to build the wrapper):
#   docker buildx build --platform linux/amd64 --load \
#     -f tasks/harness-design/Dockerfile -t candidate .
ARG BASE_IMAGE=docker.io/zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f
FROM ${BASE_IMAGE}

# program.md startup hypothesis: egress is disabled, so usage telemetry can
# only fail or retry. A hypothesis may blank one variable; the baseline keeps
# both upstream values. Neither declaration is active in this wrapper.
# ENV LLMD_USAGE_STATS_ENDPOINT=""
# ENV LLMD_POSTHOG_TOKEN=""

ENTRYPOINT ["/llmd/llmd"]
CMD ["--io-impl=zio", "--batch-size=16", "--token-batch-size=1024", "--max-context-len=128000", "--prefill-chunk-size=1024", "--page-chunk-size=16", "--backend=auto", "--gpu-memory-fraction=0.9", "--cache-memory-fraction=0.95"]
