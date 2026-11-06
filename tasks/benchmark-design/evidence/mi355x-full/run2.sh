#!/usr/bin/env bash
# The full-run step of run.sh, restarted after regenerating the canary reference.
set -euo pipefail
LLMD_CMD="/llmd/llmd --io-impl=zio --model=/models/model"
DIGEST=docker.io/zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f
export ROCR_VISIBLE_DEVICES=0
cd /work/repo/tasks/benchmark-design/suite
echo $$ > /work/run.pid
T0=$(date +%s)
log() { echo "[$(( $(date +%s) - T0 ))s] $*"; }
log "full run"
python3 collect/capture.py run --workload workload.mi355x.json --mode full --out /work/out/full \
  --start-cmd "$LLMD_CMD" --gpu mi355x --image-digest "$DIGEST" --model-repository Qwen/Qwen3.6-27B
log "full done"
cp workload.mi355x.json reference.mi355x.jsonl /work/out/
tar -C /work -czf /work/results.tgz out server-setup.log run1.log run.log
log "packed $(du -sh /work/results.tgz | cut -f1)"
touch /work/DONE
