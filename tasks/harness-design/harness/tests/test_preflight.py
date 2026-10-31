"""Host inspection rejects plausible but incomplete/corrupt inventory without secrets."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from abstract_harness import preflight


class InventoryTest(unittest.TestCase):
    def test_healthy_gpu_producer_preserves_name_evidence_for_both_smi_formats(self):
        cases = (
            (
                {"card0": {"Card series": "AMD Instinct MI355X", "PCI Bus": "0000:01:00.0"}},
                False,
            ),
            (
                [{"asic": {"market_name": "AMD Instinct MI355X"}, "bus": {"bdf": "0000:01:00.0"}}],
                True,
            ),
        )
        for smi_data, amd_fallback in cases:
            with self.subTest(amd_fallback=amd_fallback), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                drm = root / "sys/class/drm"
                sysdev = drm / "renderD128/device"
                sysdev.mkdir(parents=True)
                (sysdev / "vendor").write_text("0x1002\n")
                (sysdev / "mem_info_vram_total").write_text(str(288 * 1024**3))
                devices = {
                    name: root / name.lstrip("/") for name in ("/dev/kfd", "/dev/dri/renderD128")
                }
                for device in devices.values():
                    device.parent.mkdir(parents=True, exist_ok=True)
                    device.write_bytes(b"fake character device")
                path_type, original_stat, original_resolve = Path, Path.stat, Path.resolve

                def fake_path(value, *, drm=drm, devices=devices, path_type=path_type):
                    return {"/sys/class/drm": drm, **devices}.get(value, path_type(value))

                def fake_stat(path, *args, devices=devices, original_stat=original_stat, **kwargs):
                    if path in devices.values():
                        return os.stat_result((stat.S_IFCHR, 0, 0, 1, 0, 0, 0, 0, 0, 0))
                    return original_stat(path, *args, **kwargs)

                def fake_resolve(
                    path,
                    *args,
                    sysdev=sysdev,
                    root=root,
                    original_resolve=original_resolve,
                    **kwargs,
                ):
                    if path == sysdev:
                        return root / "sys/bus/pci/devices/0000:01:00.0"
                    return original_resolve(path, *args, **kwargs)

                responses = ([(False, "")] if amd_fallback else []) + [
                    (True, json.dumps(smi_data)),
                    (True, "AMD GPU agent gfx950"),
                ]
                report = preflight.Report()
                with (
                    mock.patch.object(preflight, "Path", side_effect=fake_path),
                    mock.patch.object(path_type, "stat", autospec=True, side_effect=fake_stat),
                    mock.patch.object(
                        path_type, "resolve", autospec=True, side_effect=fake_resolve
                    ),
                    mock.patch.object(preflight, "command", side_effect=responses) as command,
                ):
                    report.guarded(
                        "gpu",
                        "Inspect assigned device",
                        lambda report=report: preflight.check_gpu(report, "/dev/dri/renderD128"),
                    )
                checks = {row["check"]: row for row in report.checks}
                self.assertEqual(set(checks), {"gpu_identity", "rocm_driver"})
                self.assertEqual(checks["gpu_identity"]["status"], "PASS")
                self.assertEqual(
                    checks["gpu_identity"]["evidence"],
                    {
                        "name": "AMD Instinct MI355X",
                        "bdf": "0000:01:00.0",
                        "vram_bytes": 288 * 1024**3,
                    },
                )
                self.assertEqual(checks["rocm_driver"]["status"], "PASS")
                self.assertEqual(command.call_count, 3 if amd_fallback else 2)

    def test_rocm_and_amd_records_do_not_mix_two_gpu_identities(self):
        cases = [
            {
                "card0": {"Card series": "MI300X", "PCI Bus": "0000:01:00.0"},
                "card1": {"Card series": "AMD Instinct MI355X", "PCI Bus": "0000:02:00.0"},
            },
            [
                {"gpu": 0, "asic": {"market_name": "MI300X"}, "bus": {"bdf": "0000:01:00.0"}},
                {"gpu": 1, "asic": {"market_name": "MI355X"}, "bus": {"bdf": "0000:02:00.0"}},
            ],
        ]
        for data in cases:
            records = preflight.gpu_records(data)
            self.assertEqual(len(records), 2)
            selected = [r for r in records if r["bdf"] == "0000:01:00.0"]
            self.assertEqual(selected, [{"name": "MI300X", "bdf": "0000:01:00.0"}])

    def test_device_node_or_model_name_alone_never_certifies_gpu(self):
        for data in ({"market_name": "MI355X"}, {"card0": {"PCI Bus": "0000:01:00.0"}}):
            self.assertEqual(preflight.gpu_records(data), [])

    def test_aggregate_diagnostic_cannot_lend_its_model_to_another_gpu(self):
        data = {
            "diagnostic": {"market_name": "MI355X"},
            "gpu": {"card series": "MI300X", "bdf": "0000:01:00.0"},
        }
        self.assertEqual(preflight.gpu_records(data), [{"name": "MI300X", "bdf": "0000:01:00.0"}])

    def test_two_partial_gpu_records_never_combine_identity(self):
        data = {"gpu0": {"market_name": "AMD Instinct MI355X"}, "gpu1": {"bdf": "0000:01:00.0"}}
        self.assertEqual(preflight.gpu_records(data), [])

    def test_wrong_type_json_becomes_redacted_structured_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text("[]")
            (root / ".abstract-model.json").write_text("[]")
            report = preflight.Report()
            report.guarded(
                "registry",
                "Correct JSON",
                lambda: preflight.registry_readiness(root, "registry.example/team/image"),
            )
            report.guarded(
                "model", "Correct JSON", lambda: preflight.model_inventory(root, {}, None, False)
            )

            def empty_inspect():
                json.loads("[]")[0]

            report.guarded("docker", "Correct JSON", empty_inspect)
            self.assertEqual([c["status"] for c in report.checks], ["FAIL"] * 3)

    def test_registry_credentials_and_exception_values_are_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "sentinel-private-credential"
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "auths": {"registry.example": {"auth": secret}},
                    }
                )
            )
            configured, detail = preflight.registry_readiness(root, "registry.example/team/image")
            self.assertTrue(configured)
            report = preflight.Report()
            report.add("registry", "PASS", detail)

            def fail():
                raise ValueError(secret)

            report.guarded("invalid", "Correct the file", fail)
            text = json.dumps(report.result())
            self.assertNotIn(secret, text)
            self.assertNotIn('"auth"', text)
            self.assertIn("FAIL", text)
            (root / "config.json").write_text(json.dumps({"auths": {"registry.example": {}}}))
            self.assertFalse(preflight.registry_readiness(root, "registry.example/team/image")[0])

    def test_registry_rejects_signed_urls_and_userinfo(self):
        for ref in (
            "https://registry.example/team/image?token=secret",
            "user:secret@registry.example/x",
        ):
            with self.assertRaises(ValueError):
                preflight.registry_readiness(Path("/missing"), ref)

    def test_unreadable_docker_storage_does_not_discard_other_daemon_checks(self):
        report = preflight.Report()
        responses = [
            (True, '"unix:///var/run/docker.sock"'),
            (
                True,
                json.dumps(
                    {
                        "OSType": "linux",
                        "Architecture": "x86_64",
                        "DockerRootDir": "/private-docker",
                        "ServerVersion": "29.0",
                    }
                ),
            ),
            (True, "github.com/docker/buildx v0.25.0"),
            (
                True,
                json.dumps(
                    [
                        {
                            "Architecture": "amd64",
                            "Os": "linux",
                            "Config": {"Entrypoint": ["/llmd/llmd"]},
                            "Id": "sha256:abc",
                        }
                    ]
                ),
            ),
        ]
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(preflight, "command", side_effect=responses),
            mock.patch.object(preflight.shutil, "disk_usage", side_effect=PermissionError),
        ):
            preflight.check_docker(report, {"image": "pinned", "entrypoint": "/llmd/llmd"})
        statuses = {c["check"]: c["status"] for c in report.checks}
        self.assertEqual(statuses["docker_storage"], "MISSING")
        self.assertEqual(statuses["buildx"], "PASS")
        self.assertEqual(statuses["pinned_image"], "PASS")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "model"
        self.root.mkdir()
        self.pin = {"repository": "example/model", "revision": "a" * 40}
        (self.root / ".abstract-model.json").write_text(
            json.dumps({**self.pin, "status": "complete"})
        )
        for name in ("config.json", "tokenizer.json", "tokenizer_config.json"):
            (self.root / name).write_text('{"valid":true}')
        header = json.dumps({"x": {"dtype": "F32", "shape": [1], "data_offsets": [0, 4]}}).encode()
        header += b" " * (-len(header) % 8)
        (self.root / "model.safetensors").write_bytes(
            len(header).to_bytes(8, "little") + header + b"1234"
        )
        files = {}
        for path in self.root.iterdir():
            if path.name.startswith("."):
                continue
            raw = path.read_bytes()
            # Exercise both HF LFS data hashes and ordinary Git blob identities.
            identity = (
                {"sha256": hashlib.sha256(raw).hexdigest()}
                if path.suffix == ".safetensors"
                else {
                    "git_blob_sha1": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
                }
            )
            files[path.name] = {"size": len(raw), **identity}
        self.manifest = Path(self.tmp.name) / "manifest.json"
        self.manifest.write_text(json.dumps({**self.pin, "files": files}))

    def inventory(self, full=True):
        return preflight.model_inventory(self.root, self.pin, self.manifest, full)

    def test_valid_real_files_verify_both_hash_schemes(self):
        self.assertTrue(self.inventory()["verified_hashes"])
        self.assertFalse(self.inventory(False)["verified_hashes"])

    def test_same_size_tokenizer_change_fails_full_integrity(self):
        path = self.root / "tokenizer.json"
        path.write_text('{"valid":fals}')
        self.assertFalse(self.inventory(False)["verified_hashes"])
        with self.assertRaises(ValueError):
            self.inventory()

    def test_truncated_safetensors_is_rejected_before_full_hashing(self):
        path = self.root / "model.safetensors"
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(ValueError):
            self.inventory(False)

    def test_complete_marker_cannot_hide_missing_shard_or_wrong_revision(self):
        (self.root / "model.safetensors.index.json").write_text(
            json.dumps(
                {
                    "weight_map": {
                        "x": "model-00001-of-00002.safetensors",
                        "y": "model-00002-of-00002.safetensors",
                    }
                }
            )
        )
        with self.assertRaises(ValueError):
            self.inventory()
        (self.root / "model.safetensors.index.json").unlink()
        with self.assertRaises(ValueError):
            preflight.model_inventory(
                self.root, {**self.pin, "revision": "b" * 40}, self.manifest, True
            )

    def test_symlink_and_index_escape_are_rejected(self):
        path = self.root / "tokenizer.json"
        path.unlink()
        path.symlink_to(self.root / "config.json")
        with self.assertRaises(ValueError):
            self.inventory()
        path.unlink()
        path.write_text('{"valid":true}')
        (self.root / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {"x": "../outside.safetensors"}})
        )
        with self.assertRaises(ValueError):
            self.inventory()


class InspectionCommandTest(unittest.TestCase):
    def test_missing_supplied_manifest_returns_json_failure_instead_of_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with (
                mock.patch.object(preflight.platform, "system", return_value="Other"),
                redirect_stdout(output),
            ):
                code = preflight.main(
                    [
                        "--model-dir",
                        str(Path(tmp) / "missing-model"),
                        "--model-manifest",
                        str(Path(tmp) / "missing-manifest"),
                    ]
                )
            report = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "FAIL")
            self.assertIsNone(report["protected_input_sha256"]["model_manifest"])

    def test_unreadable_manifest_digest_keeps_the_final_json_report(self):
        output = io.StringIO()
        original = preflight.digest

        def digest(path, algorithm="sha256"):
            if path.name == "unreadable-manifest":
                raise PermissionError("sentinel-private-value")
            return original(path, algorithm)

        with (
            mock.patch.object(preflight.platform, "system", return_value="Other"),
            mock.patch.object(preflight, "digest", side_effect=digest),
            redirect_stdout(output),
        ):
            code = preflight.main(["--model-manifest", "/unreadable-manifest"])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 1)
        self.assertIsNone(report["protected_input_sha256"]["model_manifest"])
        self.assertNotIn("sentinel-private-value", output.getvalue())

    def test_command_drops_provider_secrets_and_bounds_hangs(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sentinel-private"}):
            ok, output = preflight.command(
                [
                    sys.executable,
                    "-c",
                    "import os; print(os.environ.get('OPENAI_API_KEY', 'absent'))",
                ]
            )
        self.assertTrue(ok)
        self.assertEqual(output.strip(), "absent")
        start = time.monotonic()
        ok, output = preflight.command(
            [sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1
        )
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - start, 2)
        self.assertNotIn("sentinel", output)
