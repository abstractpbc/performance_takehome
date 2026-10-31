"""Recovery requires exact absence; daemon failures never certify cleanup."""

from __future__ import annotations

import unittest
from unittest import mock

from abstract_harness import docker_runtime as docker
from abstract_harness.procs import RunResult


def response(stdout="", *, returncode=0, timed_out=False, aborted=False):
    return RunResult((), returncode, stdout, "", 0, timed_out, aborted)


class ContainerAbsenceTest(unittest.TestCase):
    def test_successful_empty_enumeration_proves_absence(self):
        with mock.patch.object(docker.procs, "run", return_value=response()) as cli:
            self.assertTrue(docker.container_absent("abstract-harness-candidate-12345"))
        self.assertEqual(cli.call_args.args[0], ["docker", "ps", "-a", "--format", "{{.Names}}"])
        self.assertGreater(cli.call_args.kwargs["timeout_s"], 0)
        self.assertLessEqual(cli.call_args.kwargs["timeout_s"], 30)

    def test_unrelated_and_prefix_names_do_not_match_exact_saved_container(self):
        with mock.patch.object(docker.procs, "run", return_value=response(
            "abstract-harness-candidate-12345-other\nother-server\n"
        )):
            self.assertTrue(docker.container_absent("abstract-harness-candidate-12345"))

    def test_surviving_exact_name_is_not_absent(self):
        with mock.patch.object(docker.procs, "run", return_value=response(
            "unrelated-server\nabstract-harness-candidate-12345\n"
        )):
            self.assertFalse(docker.container_absent("abstract-harness-candidate-12345"))

    def test_daemon_failure_timeout_abort_and_launch_failure_remain_unknown(self):
        cases = (response(returncode=1), response(returncode=None, timed_out=True),
                 response(aborted=True), OSError("fixture launch failure"))
        for result in cases:
            with self.subTest(result=result):
                with mock.patch.object(docker.procs, "run") as cli:
                    if isinstance(result, Exception):
                        cli.side_effect = result
                    else:
                        cli.return_value = result
                    with self.assertRaises(docker.DockerError):
                        docker.container_absent("abstract-harness-candidate-12345")

    def test_malformed_enumeration_and_invalid_saved_name_are_rejected(self):
        for output in ("permission denied\n", "good-name\n\n", "-invalid\n"):
            with self.subTest(output=output), mock.patch.object(
                docker.procs, "run", return_value=response(output)
            ):
                with self.assertRaises(docker.DockerError):
                    docker.container_absent("abstract-harness-candidate-12345")
        with mock.patch.object(docker.procs, "run") as cli:
            for name in ("", "-all", "a/b", None):
                with self.subTest(name=name), self.assertRaises(docker.DockerError):
                    docker.container_absent(name)
            cli.assert_not_called()
