"""Current-process cgroup capacity, including inherited and hidden limits."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from abstract_harness import preflight

GIB = preflight.GIB


def v2_fixture():
    return {
        "/proc/self/cgroup": "0::/tenant/job\n",
        "/proc/self/mountinfo": "20 1 0:29 / /cg rw - cgroup2 cgroup rw\n",
        "/proc/meminfo": f"MemTotal: {512 * GIB // 1024} kB\n",
        "/cg/cgroup.controllers": "cpu memory\n",
        "/cg/tenant/cpu.max": "3200000 100000\n",
        "/cg/tenant/memory.max": str(256 * GIB),
        "/cg/tenant/job/cpu.max": "max 100000\n",
        "/cg/tenant/job/memory.max": "max\n",
    }


def v1_fixture():
    files = {
        "/proc/self/cgroup": "2:cpu,cpuacct:/tenant/job\n3:memory:/tenant/job\n",
        "/proc/self/mountinfo": (
            "20 1 0:29 / /cpu rw - cgroup cgroup rw,cpu,cpuacct\n"
            "21 1 0:30 / /memory rw - cgroup cgroup rw,memory\n"
        ),
    }
    for root in ("/cpu", "/memory"):
        files[root + "/cgroup.sane_behavior"] = "0"
    for suffix in ("", "/tenant", "/tenant/job"):
        files[f"/cpu{suffix}/cpu.cfs_quota_us"] = "-1"
        files[f"/cpu{suffix}/cpu.cfs_period_us"] = "100000"
        files[f"/memory{suffix}/memory.limit_in_bytes"] = str(512 * GIB)
        files[f"/memory{suffix}/memory.use_hierarchy"] = "1"
    files["/cpu/tenant/cpu.cfs_quota_us"] = "150000"
    files["/memory/tenant/memory.limit_in_bytes"] = str(32 * GIB)
    return files


class CgroupCapacityTests(unittest.TestCase):
    def inspect(self, files, *, capacity=False, affinity=64):
        reads = []

        def read(path, *args, **kwargs):
            key = path.as_posix()
            reads.append(key)
            if key not in files:
                raise FileNotFoundError(key)
            value = files[key]
            if isinstance(value, Exception):
                raise value
            return value

        with mock.patch.object(Path, "read_text", new=read):
            if not capacity:
                return preflight.cgroup_limits(), reads
            with (
                mock.patch.object(
                    preflight.os,
                    "sched_getaffinity",
                    return_value=set(range(affinity)),
                    create=True,
                ),
                mock.patch.object(
                    preflight.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(free=1024 * GIB, total=16 * GIB),
                ),
            ):
                report = preflight.Report()
                preflight.check_capacity(report, Path("/work"), 100)
                return next(c for c in report.checks if c["check"] == "cpu_ram"), reads

    def test_nested_v2_parent_quota_and_memory_bound_the_process(self):
        files = v2_fixture()
        files["/cg/tenant/cpu.max"] = "800000 100000"
        files["/cg/tenant/memory.max"] = str(64 * GIB)
        row, reads = self.inspect(files, capacity=True)
        self.assertEqual(row["status"], "FAIL")
        self.assertEqual(row["evidence"]["cpu_capacity"], 8)
        self.assertEqual(row["evidence"]["effective_memory_bytes"], 64 * GIB)
        self.assertIn("/cg/tenant/job/cpu.max", reads)
        self.assertIn("/cg/tenant/cpu.max", reads)

    def test_complete_v2_hierarchy_can_pass_but_affinity_still_bounds_capacity(self):
        self.assertEqual(self.inspect(v2_fixture(), capacity=True)[0]["status"], "PASS")
        self.assertEqual(
            self.inspect(v2_fixture(), capacity=True, affinity=12)[0]["status"], "FAIL"
        )

    def test_v2_actual_global_root_need_not_have_non_root_limit_files(self):
        files = v2_fixture()
        files["/proc/self/cgroup"] = "0::/\n"
        row, _ = self.inspect(files, capacity=True)
        self.assertEqual(row["status"], "PASS")

    def test_bind_subtree_maps_relative_path_and_reports_hidden_ancestors(self):
        files = v2_fixture()
        files["/proc/self/mountinfo"] = "20 1 0:29 /tenant /cg rw - cgroup2 cgroup rw\n"
        files["/cg/cpu.max"] = "max 100000"
        files["/cg/memory.max"] = str(256 * GIB)
        files["/cg/job/cpu.max"] = "3200000 100000"
        files["/cg/job/memory.max"] = "max"
        row, reads = self.inspect(files, capacity=True)
        self.assertEqual(row["status"], "MISSING")
        limits = row["evidence"]["cgroup_limits"]
        self.assertEqual(limits["cpu"], 32)
        self.assertEqual(limits["memory"], 256 * GIB)
        self.assertTrue(all(h["hidden_ancestors"] for h in limits["hierarchies"]))
        self.assertIn("/cg/job/cpu.max", reads)
        self.assertNotIn("/cg/tenant/job/cpu.max", reads)

    def test_broader_mount_observes_ancestors_hidden_by_another_bind(self):
        files = v2_fixture()
        files["/proc/self/mountinfo"] += "21 1 0:29 /tenant /bound rw - cgroup2 cgroup rw\n"
        files["/cg/tenant/cpu.max"] = "200000 100000"
        result, _ = self.inspect(files)
        self.assertTrue(result["established"], result)
        self.assertEqual(result["cpu"], 2)

    def test_namespaced_root_is_not_mistaken_for_global_unlimited_root(self):
        for marker in ("cgroup.type", "cpu.max", "memory.max"):
            files = v2_fixture()
            files["/proc/self/cgroup"] = "0::/\n"
            files[f"/cg/{marker}"] = {
                "cgroup.type": "domain",
                "cpu.max": "max 100000",
                "memory.max": "max",
            }[marker]
            with self.subTest(marker=marker):
                self.assertEqual(self.inspect(files, capacity=True)[0]["status"], "MISSING")

    def test_escaped_mountpoint_is_decoded_once(self):
        files = v2_fixture()
        files["/proc/self/mountinfo"] = "20 1 0:29 / /cg\\040space rw - cgroup2 cgroup rw\n"
        for key, value in list(files.items()):
            if key.startswith("/cg/"):
                files[key.replace("/cg/", "/cg space/", 1)] = value
                del files[key]
        result, reads = self.inspect(files)
        self.assertTrue(result["established"], result)
        self.assertIn("/cg space/tenant/job/cpu.max", reads)

    def test_unmapped_or_traversing_namespace_paths_are_unestablished(self):
        for group in ("/other/job", "/../job", "/tenant/../job"):
            files = v2_fixture()
            files["/proc/self/cgroup"] = f"0::{group}\n"
            files["/proc/self/mountinfo"] = "20 1 0:29 /tenant /cg rw - cgroup2 cgroup rw\n"
            with self.subTest(group=group):
                self.assertEqual(self.inspect(files, capacity=True)[0]["status"], "MISSING")

    def test_absent_unreadable_and_malformed_limits_never_pass(self):
        for path, value in (
            ("/cg/tenant/cpu.max", "max 0"),
            ("/cg/tenant/cpu.max", "-2 100000"),
            ("/cg/tenant/cpu.max", "1.5 100000"),
            ("/cg/tenant/memory.max", "NaN"),
            ("/cg/tenant/memory.max", "-1"),
            ("/cg/tenant/job/cpu.max", PermissionError("private-sentinel")),
            ("/cg/tenant/job/memory.max", FileNotFoundError()),
        ):
            files = v2_fixture()
            files[path] = value
            with self.subTest(path=path, value=type(value).__name__):
                row, _ = self.inspect(files, capacity=True)
                self.assertEqual(row["status"], "MISSING")
                self.assertNotIn("private-sentinel", str(row))

    def test_v1_split_controllers_resolve_their_own_nested_ancestors(self):
        files = v1_fixture()
        result, _ = self.inspect(files)
        self.assertTrue(result["established"], result)
        self.assertEqual(result["cpu"], 1.5)
        self.assertEqual(result["memory"], 32 * GIB)
        del files["/cpu/cgroup.sane_behavior"]
        self.assertFalse(self.inspect(files)[0]["established"])

    def test_v1_missing_hierarchy_support_and_invalid_quota_are_unestablished(self):
        for path, value in (
            ("/memory/tenant/memory.use_hierarchy", "0"),
            ("/cpu/tenant/cpu.cfs_quota_us", "-2"),
            ("/cpu/tenant/cpu.cfs_period_us", "0"),
        ):
            files = v1_fixture()
            files[path] = value
            self.assertFalse(self.inspect(files)[0]["established"])

    def test_hybrid_host_resolves_cpu_v1_and_memory_v2_separately(self):
        files = {key: value for key, value in v2_fixture().items() if not key.endswith("cpu.max")}
        files["/proc/self/cgroup"] = "0::/tenant/job\n2:cpu,cpuacct:/tenant/job\n"
        files["/proc/self/mountinfo"] += "21 1 0:30 / /cpu rw - cgroup cgroup rw,cpu,cpuacct\n"
        files["/cg/cgroup.controllers"] = "memory"
        files.update({key: value for key, value in v1_fixture().items() if key.startswith("/cpu/")})
        result, _ = self.inspect(files)
        self.assertTrue(result["established"], result)
        self.assertEqual(result["cpu"], 1.5)
        self.assertEqual(result["memory"], 256 * GIB)
        self.assertEqual({h["version"] for h in result["hierarchies"]}, {1, 2})

    def test_malformed_metadata_and_changed_membership_are_unestablished(self):
        for path, value in (
            ("/proc/self/cgroup", "not a cgroup record"),
            ("/proc/self/cgroup", "5::/tenant/job"),
            ("/proc/self/mountinfo", "not mount metadata"),
            ("/proc/self/cgroup", PermissionError()),
        ):
            files = v2_fixture()
            files[path] = value
            self.assertFalse(self.inspect(files)[0]["established"])
        files = v2_fixture()
        original = preflight._cgroup_read

        def migrate(path):
            value = original(path)
            files["/proc/self/cgroup"] = "0::/tenant/new-job\n"
            return value

        with mock.patch.object(preflight, "_cgroup_read", side_effect=migrate):
            result, _ = self.inspect(files)
        self.assertFalse(result["established"])
        self.assertIn("changed during inspection", " ".join(result["issues"]))

    def test_missing_controller_and_ambiguous_mount_are_not_unlimited(self):
        files = v2_fixture()
        files["/proc/self/cgroup"] = "2:cpu:/tenant/job\n"
        self.assertFalse(self.inspect(files)[0]["established"])
        files = v2_fixture()
        files["/proc/self/mountinfo"] += "21 1 0:29 / /other rw - cgroup2 cgroup rw\n"
        self.assertFalse(self.inspect(files)[0]["established"])


if __name__ == "__main__":
    unittest.main()
