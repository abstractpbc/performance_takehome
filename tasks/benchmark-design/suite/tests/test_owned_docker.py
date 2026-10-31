"""Regression for daemon-owned engines outliving an attached Docker client."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("owned_docker", SUITE / "owned_docker.py")
owned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owned)
OWNER = "a" * 32
NAME = "abstract-t1-" + OWNER
IMAGE = "sha256:" + "b" * 64
ID = "c" * 64
ARGS = ["docker", "run", "--rm", "--pull=never", "--label",
        "abstract.task1.capture=" + OWNER, "--name", NAME, IMAGE]


class OwnedDockerTests(unittest.TestCase):
    def test_exact_identity_required(self):
        self.assertEqual(owned.launch_identity(ARGS), (NAME, OWNER, IMAGE))
        value = {"Id": ID, "Name": "/" + NAME, "Image": IMAGE,
                 "Config": {"Image": IMAGE, "Labels": {owned.LABEL: OWNER}}}
        self.assertEqual(owned.exact_container(value, NAME, OWNER, IMAGE), ID)
        for key, wrong in [("Id", "short"), ("Name", "/foreign"), ("Image", "foreign")]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                owned.exact_container({**value, key: wrong}, NAME, OWNER, IMAGE)
        with self.assertRaises(ValueError):
            owned.exact_container(
                {**value, "Config": {"Image": IMAGE, "Labels": {}}}, NAME, OWNER, IMAGE
            )

    @unittest.skipUnless(os.name == "posix", "assigned Linux process lifecycle")
    def test_two_stops_remove_daemon_state_before_next_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # The fake attached client leaves daemon state behind on SIGTERM,
            # matching the failure observed on the real GPU. Only exact-ID rm
            # removes it. No Docker daemon or GPU is needed for this regression.
            docker = root / "docker"
            docker.write_text("#!" + sys.executable + "\n" + r'''
import json,os,signal,sys,time
from pathlib import Path
p=Path(os.environ['FAKE_STATE'])
a=sys.argv[1:]
if a[0]=='run':
 if p.exists(): sys.exit(9)
 value=json.dumps({'Id':'c'*64,'Name':'/'+a[a.index('--name')+1],
 'Image':a[-1],'Config':{'Image':a[-1],'Labels':{'abstract.task1.capture':'a'*32}}})
 late=os.environ['FAKE_LATE_CREATE']=='1'
 def stopped(*unused):
  if late:p.write_text(value)
  sys.exit(0)
 signal.signal(signal.SIGTERM,stopped)
 if not late:p.write_text(value)
 p.with_suffix('.ready').write_text('ready')
 while True:time.sleep(.02)
elif a[0]=='inspect':
 if not p.exists():print('No such object',file=sys.stderr);sys.exit(1)
 print('['+p.read_text()+']')
elif a[:2]==['rm','--force']:
 v=json.loads(p.read_text())
 if a[2]!=v['Id']:sys.exit(8)
 p.unlink()
else:sys.exit(7)
''', encoding="utf-8")
            docker.chmod(0o700)
            state = root / "daemon.json"
            env = {**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                   "FAKE_STATE": str(state)}
            for late in (False, False, True):
                ready = state.with_suffix('.ready')
                ready.unlink(missing_ok=True)
                env['FAKE_LATE_CREATE'] = str(int(late))
                proc = subprocess.Popen([sys.executable, str(SUITE / "owned_docker.py"), *ARGS],
                                        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    end = time.monotonic() + 3
                    while not ready.exists() and time.monotonic() < end:
                        time.sleep(.01)
                    self.assertTrue(ready.exists(), "fake client did not launch")
                    proc.terminate()
                    stdout, stderr = proc.communicate(timeout=9)
                    self.assertEqual(proc.returncode, 0, stderr)
                    self.assertFalse(state.exists(), "warm engine survived cold-launch stop")
                    self.assertEqual(json.loads(stdout)["container_id"], ID)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait()


if __name__ == "__main__":
    unittest.main()
