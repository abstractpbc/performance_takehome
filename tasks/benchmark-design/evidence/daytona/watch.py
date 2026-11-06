"""Poll the run every 2 min; on DONE, death, repeated errors or the spend cap, pull results and delete.

Status checks are a few shell builtins, so they cost the measurement nothing. Results are packed and
pulled once, at the end, because compressing hundreds of MB mid-run would compete with the client.
"""

import os
import sys
import time

from daytona import Daytona

import dt

CAP_USD = float(os.environ.get("DT_CAP_USD", 23.6))  # padded spend for this sandbox
EVERY_S = int(os.environ.get("DT_EVERY_S", 120))
RESULTS = dt.HERE / os.environ.get("DT_RESULTS", "results")
STATUS = ("if test -f /work/DONE; then echo DONE; "
          "elif kill -0 $(cat /work/run.pid) 2>/dev/null; then echo RUNNING; else echo DIED; fi; "
          "grep -a '^\\[[0-9]*s\\]' /work/run.log | tail -n 1; "
          f"echo requests $(cat /work/out/{os.environ.get('DT_OUT', 'full')}/requests.jsonl 2>/dev/null | wc -l)")
PACK = ("cd /work && tar -czf partial.tgz --ignore-failed-read out run.log server-setup.log "
        "download.log 2>/dev/null; ls -la partial.tgz")


def spent() -> float:
    return (time.time() - dt.state()["created"]) / 3600 * dt.RATE_USD_H


def pull(box, remote: str, name: str) -> None:
    try:
        box.fs.download_file(remote, str(RESULTS / name))
        print("pulled", name, (RESULTS / name).stat().st_size, "bytes", flush=True)
    except Exception as error:
        print("pull failed:", name, error, flush=True)


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    d = Daytona()
    errors = 0
    while True:
        box = None
        try:
            box = dt.sandbox(d)
            status, *tail = box.process.exec(STATUS, timeout=60).result.strip().splitlines()
        except Exception as error:  # network blip, or the TTL already fired
            status, tail = "ERROR", [f"{type(error).__name__}: {error}"[:300]]
        errors = errors + 1 if status == "ERROR" else 0
        print(time.strftime("%H:%M:%S"), status, f"~${spent():.2f}", *tail, sep=" | ", flush=True)
        if status == "DONE" or status == "DIED" or errors >= 3 or spent() >= CAP_USD:
            if box is not None:
                if status == "DONE":
                    pull(box, "/work/results.tgz", "results.tgz")
                else:
                    print(box.process.exec(PACK, timeout=600).result, flush=True)
                    pull(box, "/work/partial.tgz", "partial.tgz")
            try:
                d.delete(dt.sandbox(d))
                print("sandbox deleted", flush=True)
            except Exception as error:
                print("delete failed:", error, flush=True)
            dt.save(deleted=time.time())
            print(dt.cost(), flush=True)
            return 0 if status == "DONE" else 1
        time.sleep(EVERY_S)


if __name__ == "__main__":
    sys.exit(main())
