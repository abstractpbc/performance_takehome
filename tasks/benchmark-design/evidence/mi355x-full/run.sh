#!/usr/bin/env bash
# One validation run: download, reference, capacity sweep, then the full capture.
# Every step logs elapsed seconds; $WORK/DONE marks success. Env overrides exist for the local rehearsal.
set -euo pipefail
WORK=${WORK:-/work}
PLAN=${PLAN:-workload.json}
GPU=${GPU:-mi355x}
MODEL_REPO=${MODEL_REPO:-Qwen/Qwen3.6-27B}
MODEL_REV=${MODEL_REV:-6a9e13bd6fc8f0983b9b99948120bc37f49c13e9}
DIGEST=${DIGEST:-docker.io/zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f}
SWEEP_ARGS=${SWEEP_ARGS:-}
LLMD_CMD="/llmd/llmd --io-impl=zio --model=/models/model"
URL=http://127.0.0.1:8000
export ROCR_VISIBLE_DEVICES=0

cd "$WORK/repo/tasks/benchmark-design/suite"
echo $$ > "$WORK/run.pid"
T0=$(date +%s)
log() { echo "[$(( $(date +%s) - T0 ))s] $*"; }
ready() {
  until python3 -c "import httpx, sys; sys.exit(httpx.get('$URL/v1/models', timeout=2).status_code != 200)" 2>/dev/null; do
    kill -0 "$1" 2>/dev/null || { log "setup server exited"; tail -n 30 "$WORK/server-setup.log"; return 1; }
    sleep 1
  done
}

log "devices: $(ls /dev/kfd /dev/dri 2>&1 | tr '\n' ' ')"
log "cpus $(nproc), $(grep MemTotal /proc/meminfo), disk free $(df -h /models | awk 'NR==2 {print $4}')"
if [ "${DOWNLOAD:-1}" = 1 ]; then
  log "download $MODEL_REPO@$MODEL_REV"
  hf download "$MODEL_REPO" --revision "$MODEL_REV" --local-dir /models/model > "$WORK/download.log" 2>&1
  log "downloaded $(du -sh /models/model | cut -f1)"
fi

log "setup server start"
$LLMD_CMD > "$WORK/server-setup.log" 2>&1 &
SERVER=$!
ready "$SERVER"
log "setup server ready"
python3 collect/capture.py reference --workload "$PLAN" --out "reference.$GPU.jsonl"
log "reference $(wc -l < "reference.$GPU.jsonl") entries"
# shellcheck disable=SC2086
python3 collect/sweep.py --workload "$PLAN" --base-url "$URL" --out "$WORK/out/sweep" \
  --write-plan "workload.$GPU.json" $SWEEP_ARGS
log sweep
kill "$SERVER"; wait "$SERVER" || true

python3 - "workload.$GPU.json" "reference.$GPU.jsonl" <<'PY'
import hashlib, json, sys
from pathlib import Path
plan_path, ref = Path(sys.argv[1]), Path(sys.argv[2])
plan = json.loads(plan_path.read_text())
# Relative to the plan, so plan and reference evaluate anywhere as a pair.
plan["reference"] = {"path": ref.name, "sha256": hashlib.sha256(ref.read_bytes()).hexdigest()}
# Provisional, from the 3060 A/A pairs (0 of 82 anchors diverged).
plan["gates"]["max_anchor_divergence"] = 0.1
plan_path.write_text(json.dumps(plan, indent=2) + "\n")
print("rates", {cell: c["arrival"]["rate_rps"] for cell, c in plan["cells"].items()})
PY
log "full run"
python3 collect/capture.py run --workload "workload.$GPU.json" --mode full --out "$WORK/out/full" \
  --start-cmd "$LLMD_CMD" --gpu "$GPU" --image-digest "$DIGEST" --model-repository "$MODEL_REPO"
log "full done"
cp "workload.$GPU.json" "reference.$GPU.jsonl" "$WORK/out/"
tar -C "$WORK" -czf "$WORK/results.tgz" out server-setup.log run.log
log "packed $(du -sh "$WORK/results.tgz" | cut -f1)"
touch "$WORK/DONE"
