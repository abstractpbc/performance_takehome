#!/usr/bin/env bash
# Targeted run: the three underpowered cells, 2 powered repetitions each, one launch.
set -euo pipefail
LLMD_CMD="/llmd/llmd --io-impl=zio --model=/models/model"
DIGEST=docker.io/zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f
export ROCR_VISIBLE_DEVICES=0
cd /work/repo/tasks/benchmark-design/suite
echo $$ > /work/run.pid
T0=$(date +%s)
log() { echo "[$(( $(date +%s) - T0 ))s] $*"; }
timeout 20 /llmd/llmd --help > /work/llmd-help.txt 2>&1 || true
log "download"
hf download Qwen/Qwen3.6-27B --revision 6a9e13bd6fc8f0983b9b99948120bc37f49c13e9 \
  --local-dir /models/model > /work/download.log 2>&1
log "downloaded $(du -sh /models/model | cut -f1)"
log "fill run"
python3 collect/capture.py run --workload workload.mi355x-fill.json --mode fill --out /work/out/fill \
  --start-cmd "$LLMD_CMD" --gpu mi355x --image-digest "$DIGEST" --model-repository Qwen/Qwen3.6-27B
log "fill done"
tar -C /work -czf /work/results.tgz out run.log download.log llmd-help.txt
log "packed $(du -sh /work/results.tgz | cut -f1)"
touch /work/DONE
