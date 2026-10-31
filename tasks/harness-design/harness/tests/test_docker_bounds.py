"""Operation-wide Docker deadlines and fail-closed internal-network reuse."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from abstract_harness import docker_runtime as docker
from abstract_harness.procs import RunResult


def result(args, *, ok=True, payload=None, timed_out=False):
    return RunResult(
        tuple(args),
        0 if ok else 1,
        json.dumps(payload) if payload is not None else "",
        "",
        0,
        timed_out,
    )


class DockerOperationBoundsTests(unittest.TestCase):
    def test_active_helpers_never_start_a_cli_after_deadline_or_abort(self):
        helpers = (
            lambda **kw: docker.image_config("image", **kw),
            lambda **kw: docker.image_exists("image", **kw),
            lambda **kw: docker.image_cmd("image", **kw),
            lambda **kw: docker.image_entrypoint("image", **kw),
            lambda **kw: docker.resolve_digest("image", **kw),
            lambda **kw: docker.retag("image", "last-good", **kw),
            lambda **kw: docker.container_state("container", **kw),
            lambda **kw: docker.container_running("container", **kw),
            lambda **kw: docker.container_ip("container", "network", **kw),
            lambda **kw: docker.container_logs("container", **kw),
            lambda **kw: docker.run_detached(["docker", "run", "--detach", "image"], **kw),
            lambda **kw: docker.ensure_internal_network("network", **kw),
        )
        for helper in helpers:
            for options in (
                {"deadline_monotonic": 10},
                {"deadline_monotonic": 20, "should_abort": lambda: True},
            ):
                with self.subTest(helper=helper, options=options):
                    with mock.patch.object(docker.time, "monotonic", return_value=10):
                        with mock.patch.object(docker.procs, "run") as cli:
                            with self.assertRaises(docker.DockerError):
                                helper(timeout_s=60, **options)
                            cli.assert_not_called()

    def test_each_active_inspect_helper_clamps_to_the_outer_deadline(self):
        payload = [
            {
                "Id": "sha256:config",
                "RepoDigests": ["repo@sha256:manifest"],
                "Config": {"Cmd": ["--flag"], "Entrypoint": ["/llmd"]},
                "State": {"Running": True},
                "NetworkSettings": {"Networks": {"network": {"IPAddress": "172.18.0.2"}}},
                "Internal": True,
            }
        ]
        helpers = (
            lambda **kw: docker.image_config("image", **kw),
            lambda **kw: docker.image_exists("image", **kw),
            lambda **kw: docker.image_cmd("image", **kw),
            lambda **kw: docker.image_entrypoint("image", **kw),
            lambda **kw: docker.resolve_digest("image", **kw),
            lambda **kw: docker.retag("image", "last-good", **kw),
            lambda **kw: docker.container_state("container", **kw),
            lambda **kw: docker.container_running("container", **kw),
            lambda **kw: docker.container_ip("container", "network", **kw),
            lambda **kw: docker.container_logs("container", **kw),
            lambda **kw: docker.run_detached(["docker", "run", "--detach", "image"], **kw),
            lambda **kw: docker.ensure_internal_network("network", **kw),
        )

        def abort():
            return False

        for helper in helpers:
            with self.subTest(helper=helper):
                with mock.patch.object(docker.time, "monotonic", return_value=10):
                    with mock.patch.object(docker.procs, "run") as cli:
                        cli.side_effect = lambda args, **kw: result(args, payload=payload)
                        helper(timeout_s=60, should_abort=abort, deadline_monotonic=10.25)
                        self.assertEqual(cli.call_args.kwargs["timeout_s"], 0.25)
                        self.assertIs(cli.call_args.kwargs["should_abort"], abort)

    def test_network_probe_create_and_race_recheck_share_one_original_budget(self):
        clock = [100.0]
        budgets = []

        def slow_cli(args, **kwargs):
            budgets.append(kwargs["timeout_s"])
            clock[0] += (2, 2, 0.25)[len(budgets) - 1]
            return result(args, ok=len(budgets) == 3, payload=[{"Internal": True}])

        with mock.patch.object(docker.time, "monotonic", side_effect=lambda: clock[0]):
            with mock.patch.object(docker.procs, "run", side_effect=slow_cli):
                self.assertEqual(
                    docker.ensure_internal_network(
                        "network", timeout_s=100, deadline_monotonic=105
                    ),
                    "network",
                )
        self.assertEqual(budgets, [5, 3, 1])

    def test_network_deadline_during_failed_probe_prevents_create(self):
        clock = [100.0]

        def slow_cli(args, **kwargs):
            clock[0] = 105
            return result(args, ok=False, timed_out=True)

        with mock.patch.object(docker.time, "monotonic", side_effect=lambda: clock[0]):
            with mock.patch.object(docker.procs, "run", side_effect=slow_cli) as cli:
                with self.assertRaises(docker.DockerError):
                    docker.ensure_internal_network("network", deadline_monotonic=105)
                self.assertEqual(cli.call_count, 1)

    def test_abort_between_probe_and_create_prevents_create(self):
        stopped = [False]

        def cli_probe(args, **kwargs):
            stopped[0] = True
            return result(args, ok=False)

        with mock.patch.object(docker.procs, "run", side_effect=cli_probe) as cli:
            with self.assertRaises(docker.DockerError):
                docker.ensure_internal_network("network", should_abort=lambda: stopped[0])
            self.assertEqual(cli.call_count, 1)

    def test_fast_success_after_abort_is_not_reported_successful(self):
        stopped = [False]

        def cli_tag(args, **kwargs):
            stopped[0] = True
            return result(args)

        with mock.patch.object(docker.procs, "run", side_effect=cli_tag):
            with self.assertRaises(docker.DockerError):
                docker.retag("image", "last-good", should_abort=lambda: stopped[0])

    def test_postbuild_inspection_uses_only_the_unspent_build_budget(self):
        clock = [100.0]
        budgets = []

        def slow_cli(args, **kwargs):
            budgets.append(kwargs["timeout_s"])
            clock[0] += 4.75 if len(budgets) == 1 else 0.1
            return result(
                args, payload=[{"Id": "sha256:config", "RepoDigests": ["repo@sha256:manifest"]}]
            )

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(docker.time, "monotonic", side_effect=lambda: clock[0]):
                with mock.patch.object(docker.procs, "run", side_effect=slow_cli):
                    built = docker.build_image(
                        Path(tmp), "image", log_path=Path(tmp) / "build.log", timeout_s=5
                    )
        self.assertTrue(built.ok)
        self.assertEqual(budgets, [5, 0.25])
        self.assertEqual(built.digest, "sha256:manifest")
        self.assertAlmostEqual(built.duration_s, 4.85)

    def test_completed_build_at_deadline_does_not_start_inspection(self):
        clock = [100.0]

        def slow_cli(args, **kwargs):
            clock[0] = 105
            return result(args)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(docker.time, "monotonic", side_effect=lambda: clock[0]):
                with mock.patch.object(docker.procs, "run", side_effect=slow_cli) as cli:
                    built = docker.build_image(
                        Path(tmp), "image", log_path=Path(tmp) / "build.log", timeout_s=5
                    )
        self.assertFalse(built.ok)
        self.assertTrue(built.timed_out)
        self.assertEqual(cli.call_count, 1)

    def test_cleanup_still_removes_after_its_independently_bounded_stop_times_out(self):
        clock = [100.0]
        calls = []

        def cli_cleanup(args, **kwargs):
            calls.append((list(args), kwargs["timeout_s"], kwargs["should_abort"]))
            clock[0] += 40 if len(calls) == 1 else 0
            return result(args, ok=len(calls) > 1, timed_out=len(calls) == 1)

        with mock.patch.object(docker.time, "monotonic", side_effect=lambda: clock[0]):
            with mock.patch.object(docker.procs, "run", side_effect=cli_cleanup):
                docker.stop_container("owned-container")
        self.assertEqual([row[1] for row in calls], [40, 60])
        self.assertEqual(calls[1][0], ["docker", "rm", "-f", "owned-container"])
        self.assertTrue(all(row[2] is None for row in calls))


class InternalNetworkTests(unittest.TestCase):
    def test_existing_noninternal_network_is_never_reused_or_modified(self):
        for internal in (False, None, "true", 1):
            with self.subTest(internal=internal):
                with mock.patch.object(docker.procs, "run") as cli:
                    cli.side_effect = lambda args, internal=internal, **kw: result(
                        args, payload=[{"Internal": internal}]
                    )
                    with self.assertRaisesRegex(docker.DockerError, "not Internal=true"):
                        docker.ensure_internal_network("existing-network")
                    self.assertEqual(cli.call_count, 1)
                    self.assertEqual(cli.call_args.args[0][1:3], ["network", "inspect"])

    def test_existing_internal_network_is_reused(self):
        with mock.patch.object(docker.procs, "run") as cli:
            cli.side_effect = lambda args, **kw: result(args, payload=[{"Internal": True}])
            self.assertEqual(docker.ensure_internal_network("network"), "network")
            self.assertEqual(cli.call_count, 1)

    def test_creation_race_and_success_both_require_inspected_internal_true(self):
        for created_ok in (False, True):
            for internal in (False, True):
                with self.subTest(created_ok=created_ok, internal=internal):
                    calls = []

                    def cli_network(
                        args, calls=calls, created_ok=created_ok, internal=internal, **kwargs
                    ):
                        calls.append(list(args))
                        if len(calls) == 1:
                            return result(args, ok=False)
                        if len(calls) == 2:
                            return result(args, ok=created_ok)
                        return result(args, payload=[{"Internal": internal}])

                    with mock.patch.object(docker.procs, "run", side_effect=cli_network):
                        if internal:
                            self.assertEqual(docker.ensure_internal_network("network"), "network")
                        else:
                            with self.assertRaises(docker.DockerError):
                                docker.ensure_internal_network("network")
                    self.assertEqual(len(calls), 3)

    def test_malformed_inspection_never_establishes_isolation(self):
        for payload in ({"Internal": True}, [], [None], ["true"]):
            with self.subTest(payload=payload):
                with mock.patch.object(docker.procs, "run") as cli:
                    cli.side_effect = lambda args, payload=payload, **kw: result(
                        args, payload=payload
                    )
                    with self.assertRaises(docker.DockerError):
                        docker.ensure_internal_network("network")
                    self.assertEqual(cli.call_count, 1)
