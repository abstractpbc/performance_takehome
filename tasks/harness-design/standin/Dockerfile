# CANDIDATE-BUILT STAND-IN ENGINE (CPU) -- not the organizer's LLMD image.
#
# Builds llama.cpp `llama-server` from a pinned source tarball and serves an
# OpenAI-compatible streaming API on port 8000 with the same contract shape the
# harness uses for LLMD: ENTRYPOINT is the engine binary, CMD holds the tunable
# runtime flags, and the harness appends the model argument at `docker run`
# time (LLMD: `--model=/models/model`, stand-in: `--model /models/model/model.gguf`).
#
# Every `ARG` below and the `CMD` line are legitimate experiment knobs: a
# proposer patch edits this file, the harness rebuilds a FRESH image per
# attempt, so stand-in improvements/regressions are real build/config effects.
#
# Base image is pinned by digest. alpine is used (not ubuntu) because the
# anonymous Docker Hub / ECR Public pull limits were exhausted on the
# development host; the pinned alpine layer was already present locally.
ARG BASE_IMAGE=alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc

FROM ${BASE_IMAGE} AS build
# llama.cpp tag v0.7.0
ARG LLAMA_CPP_COMMIT=620d2225cc436ab0b001e3200e590e3b286ef9ab
ARG CMAKE_BUILD_TYPE=Release
ARG GGML_NATIVE=OFF
ARG GGML_AVX=ON
ARG GGML_AVX2=ON
ARG GGML_FMA=ON
ARG GGML_F16C=ON
ARG GGML_AVX512=OFF
ARG GGML_LTO=OFF
ARG GGML_OPENMP=ON
ARG GGML_CPU_REPACK=ON
ARG EXTRA_CMAKE_ARGS=""
RUN apk add --no-cache build-base cmake linux-headers
# The commit ARG is part of the URL and the checksum pins the archive bytes, so
# changing one without the other fails the build (the tarball carries no .git).
ADD --checksum=sha256:1d4a987d9bd048f6e93107e838026438df70fbde237fc0c69c63d600512b4bd9 \
    https://github.com/ggml-org/llama.cpp/archive/${LLAMA_CPP_COMMIT}.tar.gz /src.tar.gz
RUN mkdir -p /src \
 && tar -xzf /src.tar.gz -C /src --strip-components=1 \
 && cmake -S /src -B /build \
      -DCMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE} \
      -DGGML_NATIVE=${GGML_NATIVE} \
      -DGGML_AVX=${GGML_AVX} -DGGML_AVX2=${GGML_AVX2} -DGGML_FMA=${GGML_FMA} \
      -DGGML_F16C=${GGML_F16C} -DGGML_AVX512=${GGML_AVX512} \
      -DGGML_LTO=${GGML_LTO} -DGGML_OPENMP=${GGML_OPENMP} \
      -DGGML_CPU_REPACK=${GGML_CPU_REPACK} \
      -DBUILD_SHARED_LIBS=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_SUBPROCESS=OFF \
      -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_TOOLS=ON \
      -DLLAMA_BUILD_SERVER=ON ${EXTRA_CMAKE_ARGS} \
 && cmake --build /build --target llama-server -j"$(nproc)" \
 && strip /build/bin/llama-server

FROM ${BASE_IMAGE} AS runtime
RUN apk add --no-cache libstdc++ libgomp
COPY --from=build /build/bin/llama-server /app/llama-server
# Mirror the LLMD contract: no network access needed, model mounted read-only
# at /models/model by the harness, server listens on 0.0.0.0:8000.
ENV LLAMA_LOG_COLORS=0
WORKDIR /
ENTRYPOINT ["/app/llama-server"]
# Runtime knobs (experiment surface). Baseline is deliberately conservative.
CMD ["--host", "0.0.0.0", "--port", "8000", "--alias", "standin", "--no-webui", "--metrics", "--ctx-size", "2048", "--threads", "4", "--threads-batch", "4", "--batch-size", "512", "--ubatch-size", "512", "--parallel", "1", "--flash-attn", "auto", "--cache-type-k", "f16", "--cache-type-v", "f16"]
