"""Rehearse the fill plan end to end against the fake server, for free, before paying for it.

Rates are scaled up so the windows take seconds; everything else (mode, cells, power rule,
evaluate) is the plan as it will run. Usage: rehearse_fill.py <suite> <fill plan> <out dir>
"""
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

SUITE, PLAN, OUT = (Path(arg).resolve() for arg in sys.argv[1:4])
PY = sys.executable
FAKE = SUITE / "tests" / "e2e" / "fake_server.py"
PORT = 18766
URL = f"http://127.0.0.1:{PORT}"
REPOSITORY, REVISION = "Qwen/Qwen3-8B", "b968826d9c46dd6066d109eabc6255188de91218"

OUT.mkdir(parents=True, exist_ok=True)
plan = json.loads(PLAN.read_text())
plan["model"].update(role="smoke", revision=REVISION)
for entry in plan["prompt_sets"].values():
    entry["path"] = str(SUITE / entry["path"])
plan["reference"]["path"] = str(OUT / "reference.jsonl")
for cell in plan["cells"].values():
    cell["arrival"]["rate_rps"] *= 20
plan["modes"]["fill"]["duration_s"] /= 20
plan["warmup"]["requests"] = 5
plan["deadlines"]["startup_timeout_s"] = 30
workload = OUT / "workload.rehearsal.json"
workload.write_text(json.dumps(plan))
capture = [PY, str(SUITE / "collect" / "capture.py")]
server = [PY, str(FAKE), "--port", str(PORT)]

setup = subprocess.Popen([*server, "--ttft", "0", "--gap", "0", "--startup", "0"])
try:
    time.sleep(0.5)
    subprocess.run([*capture, "reference", "--workload", str(workload), "--out",
                    str(OUT / "reference.jsonl"), "--base-url", URL], check=True)
finally:
    setup.terminate()
    setup.wait()
started = time.monotonic()
events = OUT / "fill"
subprocess.run([*capture, "run", "--workload", str(workload), "--mode", "fill", "--out", str(events),
                "--base-url", URL, "--gpu", "none", "--gpus", "0", "--start-cmd", shlex.join(server)],
               check=True)
print(f"capture {time.monotonic() - started:.0f} s", flush=True)
subprocess.run([PY, str(SUITE / "collect" / "tokenize_events.py"), "--events", str(events),
                "--repository", REPOSITORY, "--revision", REVISION], check=True, stderr=subprocess.DEVNULL)
subprocess.run([PY, str(SUITE / "evaluate.py"), "--events", str(events), "--workload", str(workload),
                "--out", str(OUT / "report.json")], check=True)
