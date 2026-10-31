"""Recover the exact owned container even after the wrapper's hard exit."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE))
import capture  # noqa: E402


class OwnershipCheckpoint(unittest.TestCase):
    def test_checkpoint_survives_hard_exit_before_final_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            code = (
                "import os,sys; from pathlib import Path; "
                f"sys.path.insert(0,{str(SUITE)!r}); import capture; "
                "capture.persist_ownership(Path(sys.argv[1]),name='abstract-t1-'+'a'*32,"
                "owner='a'*32,image_id='sha256:'+'b'*64,model_dir=Path(sys.argv[1])); "
                "os._exit(9)"
            )
            child = subprocess.run([sys.executable, "-B", "-c", code, str(root)], timeout=5)
            self.assertEqual(child.returncode, 9)
            saved = json.loads((root / "ownership.json").read_bytes())
            self.assertEqual(saved["container_owner"], "a" * 32)
            self.assertEqual(saved["expected_image_id"], "sha256:" + "b" * 64)
            self.assertEqual(saved["owner_label"], capture.OWNER_LABEL)
            self.assertFalse((root / "capture-receipt.json").exists())
            with self.assertRaises(FileExistsError):
                capture.persist_ownership(
                    root,
                    name="abstract-t1-" + "c" * 32,
                    owner="c" * 32,
                    image_id="sha256:" + "d" * 64,
                    model_dir=root,
                )
            self.assertEqual(json.loads((root / "ownership.json").read_bytes()), saved)

    def test_failed_durability_does_not_return_launch_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(capture.os, "fsync", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    capture.persist_ownership(
                        root,
                        name="abstract-t1-" + "a" * 32,
                        owner="a" * 32,
                        image_id="sha256:" + "b" * 64,
                        model_dir=root,
                    )

    def test_malformed_ownership_never_publishes_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(capture.CaptureError):
                capture.persist_ownership(
                    root,
                    name="unrelated-container",
                    owner="a" * 32,
                    image_id="sha256:" + "b" * 64,
                    model_dir=root,
                )
            self.assertFalse((root / "ownership.json").exists())
