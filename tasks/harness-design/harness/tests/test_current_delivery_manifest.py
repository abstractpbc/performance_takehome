"""Preserved batch4 delivery binds its historical measurements and image."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unittest
from collections import Counter

from . import _paths

TASK = _paths.HARNESS_DIR.parent
CURRENT = json.loads(
    (TASK / "submission.controller-batch4-live-20261029.json").read_text(encoding="utf-8")
)
EVIDENCE = _paths.REPO_ROOT / CURRENT["evidence"]["directory"]
BASELINE = "sha256:7db01160c57479b9d608b7c2e6fc4e6b5d438e53eb4d44dc9f3ab0441d1bb20d"
SELECTED = "sha256:84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb"
GATES = {
    "all_requests_succeeded",
    "valid_finish_reason",
    "complete_output",
    "token_count_matches_usage",
    "greedy_self_consistency",
    "golden_output_match",
}


def load(relative):
    return json.loads((EVIDENCE / relative).read_text(encoding="utf-8"))


class CurrentDeliveryManifestTests(unittest.TestCase):
    def test_llmd_readme_invocations_use_the_actual_llmd_request_profile(self):
        readme = (_paths.HARNESS_DIR / "README.md").read_text(encoding="utf-8")
        blocks = [
            b
            for b in re.findall(r"```[^\n]*\n(.*?)```", readme, flags=re.S)
            if "--engine llmd" in b
        ]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            self.assertIn(
                "--development-benchmark "
                "tasks/harness-design/harness/development-benchmark.llmd.json",
                block,
            )
        self.assertEqual(
            load("actual-source/development-benchmark.llmd.json")["request_profile"],
            "llmd_closed_thinking_v1",
        )

    def test_historical_manifest_bytes_and_assertions_are_preserved(self):
        local = (TASK / "submission.live-mi355x-20261026.json").read_bytes()
        git = (TASK / "submission.live-mi355x-20261026.git.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(local).hexdigest(),
            "c8d39e3d6ef4afa017e18e45456ea9338a616e08082b193db840a282412bd07e",
        )
        self.assertEqual(
            hashlib.sha256(git).hexdigest(),
            "0fe246c21b0d88026cb0fa34d304090aaeda7991cf8e48718da5f600b8e41d2a",
        )
        self.assertEqual(json.loads(local), json.loads(git))
        self.assertEqual(json.loads(local)["image_identity_kind"], "image-index-digest")
        self.assertFalse(json.loads(local)["delivery"]["registry_publication_verified"])

    def test_every_verbatim_evidence_and_source_hash_matches(self):
        inventory = load("inventory.json")["files"]
        for relative, expected in inventory.items():
            with self.subTest(relative=relative):
                path = EVIDENCE / relative
                self.assertTrue(path.resolve().is_relative_to(EVIDENCE.resolve()))
                raw = path.read_bytes()
                self.assertEqual(len(raw), expected["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected["sha256"])
        profile = EVIDENCE / "actual-source/development-benchmark.llmd.json"
        self.assertEqual(
            hashlib.sha256(profile.read_bytes()).hexdigest(),
            CURRENT["development_benchmark"]["sha256"],
        )
        self.assertEqual(
            CURRENT["development_benchmark"]["sha256"],
            load("baseline/result.json")["profile_sha256"],
        )

    def test_three_complete_phases_have_30_actual_requests_six_gates_and_golden_outputs(self):
        golden = load("baseline/golden.json")
        self.assertEqual(len(golden), 6)
        total = 0
        for phase in ("baseline", "control", "selected-final"):
            with self.subTest(phase=phase):
                declared = CURRENT["evidence"]["phases"][phase]
                report = load(declared["report"])
                durable = [
                    json.loads(line)
                    for line in (EVIDENCE / declared["traces"]).read_text().splitlines()
                ]
                self.assertEqual(declared["requests"], 30)
                self.assertEqual(len(report["traces"]), 30)
                self.assertEqual(len(durable), 31)
                self.assertEqual(durable[1:], report["traces"])
                self.assertTrue(report["gates_passed"])
                self.assertEqual({gate["name"] for gate in report["gates"]}, GATES)
                self.assertTrue(all(gate["passed"] for gate in report["gates"]))
                self.assertEqual(
                    Counter(t["prompt_id"] for t in report["traces"]), dict.fromkeys(golden, 5)
                )
                self.assertTrue(
                    all(t["ok"] and t["text"] == golden[t["prompt_id"]] for t in report["traces"])
                )
                total += len(report["traces"])
        self.assertEqual(total, CURRENT["evidence"]["measured_requests_total"])
        self.assertEqual(total, 90)

    def test_failed_candidate_has_no_fabricated_report_or_metric(self):
        candidate = CURRENT["evidence"]["phases"]["candidate"]
        self.assertEqual(candidate["status"], "PARTIAL_FAILURE")
        self.assertEqual(candidate["requests"], 0)
        self.assertIsNone(candidate["gates_passed"])
        self.assertIsNone(candidate["report"])
        self.assertFalse((EVIDENCE / "candidate/full-report.json").exists())
        self.assertEqual((EVIDENCE / candidate["traces"]).read_bytes(), b"")
        log = (EVIDENCE / "candidate/container.stderr").read_text()
        self.assertIn("Resolving DFlash model repo", log)
        self.assertIn("error: FileNotFound", log)
        decision = load("paired-result.json")["decision"]
        self.assertEqual(decision, CURRENT["evidence"]["decision"])
        self.assertEqual(decision["outcome"], "gate_failed")
        self.assertFalse(decision["kept"])
        with (EVIDENCE / "results.tsv").open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(
            [r["phase"] for r in rows], ["baseline", "control", "candidate", "selected-final"]
        )
        self.assertEqual(rows[2]["measured_requests"], "0")
        self.assertEqual(rows[2]["startup_s"], "")
        self.assertEqual(rows[2]["gates_passed"], "")

    def test_selected_manifest_and_rejected_config_identities_are_distinct(self):
        self.assertEqual(CURRENT["image_digest"], SELECTED)
        self.assertEqual(
            CURRENT["image"], "ghcr.io/sharonbasovich/performance_takehome-task2-llmd@" + SELECTED
        )
        self.assertEqual(CURRENT["image_identity_kind"], "linux-amd64-platform-manifest-digest")
        self.assertEqual(load("selected-image-inspect.json")[0]["Id"], BASELINE)
        self.assertEqual(CURRENT["delivery"]["platform_manifest"], SELECTED)
        manifest_raw = (EVIDENCE / "candidate-metadata/manifest.json").read_bytes()
        config_raw = (EVIDENCE / "candidate-metadata/config.json").read_bytes()
        candidate_id = "sha256:" + hashlib.sha256(manifest_raw).hexdigest()
        self.assertEqual(candidate_id, CURRENT["agent_harness"]["candidate_image_id"])
        self.assertNotEqual(candidate_id, SELECTED)
        candidate = json.loads(manifest_raw)
        self.assertEqual(
            candidate["config"]["digest"], "sha256:" + hashlib.sha256(config_raw).hexdigest()
        )
        self.assertEqual(len(candidate["layers"]), 23)
        config = json.loads(config_raw)
        selected = load("selected-image-inspect.json")[0]
        self.assertEqual(config["rootfs"]["diff_ids"], selected["RootFS"]["Layers"])
        self.assertEqual(config["config"]["Entrypoint"], selected["Config"]["Entrypoint"])
        self.assertEqual(
            config["config"]["Cmd"],
            selected["Config"]["Cmd"] + ["--speculative-drafter-model=native"],
        )
        self.assertEqual(
            hashlib.sha256((EVIDENCE / "pi/candidate.Dockerfile").read_bytes()).hexdigest(),
            CURRENT["agent_harness"]["candidate_Dockerfile_sha256"],
        )

    def test_current_provenance_limits_gain_and_separates_controller_from_pi(self):
        self.assertFalse(CURRENT["evidence"]["optimization_gain_claimed"])
        self.assertTrue(CURRENT["evaluation"]["optimization_or_speedup_claimed"])
        self.assertFalse(CURRENT["agent_harness"]["candidate_selected"])
        self.assertFalse(CURRENT["agent_harness"]["canonical_unattended_loop_executed"])
        self.assertEqual(
            CURRENT["agent_harness"]["execution_scope"], "external_stage_orchestration"
        )
        self.assertEqual(
            CURRENT["development_benchmark"]["request_profile"], "llmd_closed_thinking_v1"
        )
        self.assertFalse(CURRENT["development_benchmark"]["official_grade"])
        self.assertEqual(CURRENT["selected_experiment"]["Pi_selected_batch_value"], 1)
        self.assertEqual(CURRENT["selected_experiment"]["controller_selected_batch_value"], 4)
        self.assertFalse(CURRENT["selected_experiment"]["canonical_unattended_loop_executed"])
        self.assertFalse(CURRENT["selected_experiment"]["official_grade"])

    def test_successful_pi_metadata_excludes_message_and_argument_contents(self):
        metadata = load("pi/tool-events.json")
        self.assertEqual(metadata["stock_cli"]["version"], "1.0.0")
        self.assertEqual(metadata["effective_historical_wire_output_tokens"], 8192)
        self.assertEqual(metadata["requested_output_tokens"], 16384)
        starts = [e for e in metadata["tool_events"] if e["event"] == "tool_execution_start"]
        ends = [e for e in metadata["tool_events"] if e["event"] == "tool_execution_end"]
        self.assertEqual(len(starts), 10)
        self.assertEqual({e["tool_call_id"] for e in starts}, {e["tool_call_id"] for e in ends})
        allowed = {
            "sequence",
            "event",
            "tool_call_id",
            "tool_name",
            "is_error",
            "argument_canonical_utf8_bytes",
            "argument_canonical_sha256",
            "allowed_relative_work_filenames",
        }
        self.assertTrue(all(set(e) <= allowed for e in metadata["tool_events"]))
        self.assertEqual(metadata["assistant_stop_reason_metadata"][-1]["stop_reason"], "stop")
        self.assertEqual(
            load("pi/runtime-verification.json")["pi_distribution_files_checked"], 1243
        )

    def test_original_later_pi_completion_snapshot_remains_unmeasured(self):
        historical = json.loads((TASK / "submission.pi1-live-20261028.json").read_text())
        (pending,) = historical["pending_proposals"]
        self.assertEqual(pending["status"], "UNMEASURED_PROPOSAL_ONLY")
        self.assertFalse(pending["GPU_build_or_measurement_performed"])
        self.assertFalse(pending["selected"])
        self.assertFalse(pending["speedup_claimed"])
        self.assertIsNone(pending["candidate_image_id"])
        dockerfile = (EVIDENCE / "later-unmeasured-pi2/candidate.Dockerfile").read_text()
        self.assertIn('"--page-chunk-size=64"', dockerfile)
        self.assertNotIn('"--speculative-drafter-model=native"', dockerfile)

    def test_later_measured_pi_cycles_replay_90_real_passing_requests_and_decisions(self):
        self.assertEqual(CURRENT["pending_proposals"], [])
        self.assertEqual(len(CURRENT["additional_experiments"]), 3)
        for experiment in CURRENT["additional_experiments"]:
            with self.subTest(experiment=experiment["name"]):
                self.check_measured_cycle(experiment)
        self.assertEqual(
            sum(e["measured_requests"] for e in CURRENT["additional_experiments"])
            + CURRENT["evidence"]["measured_requests_total"],
            360,
        )

    def check_measured_cycle(self, experiment):
        from dataclasses import asdict

        from abstract_harness import decision, measure

        prefix = experiment["directory"].rsplit("/", 1)[-1] + "/"
        self.assertIn(
            experiment["status"],
            {
                "MEASURED_NO_CHANGE",
                "MEASURED_IMPROVED_PENDING_FINAL_SELECTION",
                "MEASURED_IMPROVED",
                "MEASURED_IMPROVED_SERIAL_NOT_FINAL_MIXED_SELECTION",
            },
        )
        pair = load(prefix + "paired-result.json")
        profile = load(prefix + "actual-source/profile.json")
        golden = load(prefix + "golden.json")
        samples = {}
        total = 0
        for phase in ("control", "candidate", "selected-final"):
            report = load(prefix + phase + "/full-report.json")
            durable = [
                json.loads(line)
                for line in (EVIDENCE / prefix / phase / "all-request-traces.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertEqual(len(durable), 31)
            self.assertEqual(durable[1:], report["traces"])
            self.assertEqual(
                Counter(t["prompt_id"] for t in report["traces"]), dict.fromkeys(golden, 5)
            )
            traces = [measure.RequestTrace(**t) for t in report["traces"]]
            self.assertEqual(
                [asdict(g) for g in measure.correctness_gates(traces, profile, golden)],
                report["gates"],
            )
            self.assertTrue(report["gates_passed"])
            self.assertTrue(all(g["passed"] for g in report["gates"]))
            recomputed = {}
            for trace in traces:
                rate, _ = measure.ftgr_for_traces([trace], profile)
                recomputed.setdefault(trace.prompt_id, []).append(rate)
            self.assertEqual(recomputed, report["ftgr_by_prompt"])
            samples[phase] = load(prefix + phase + "/samples.json")
            total += len(traces)
        self.assertEqual(total, experiment["measured_requests"])
        expected = decision.decide(
            samples["control"],
            samples["candidate"],
            gates_passed=True,
            min_effect=0.03,
            min_samples=3,
            expected_samples=dict.fromkeys(golden, 5),
        )
        self.assertEqual(json.loads(json.dumps(asdict(expected))), pair["decision"])
        self.assertEqual(pair["decision"], experiment["decision"])
        self.assertEqual(expected.outcome, experiment["decision"]["outcome"])
        self.assertEqual(expected.kept, experiment["decision"]["kept"])
        self.assertEqual(pair["selected_image_id"], experiment["selected_image_id"])
        self.assertEqual(
            experiment["registry_publication_required"],
            expected.kept and experiment["selected_image_id"] == CURRENT["image_digest"],
        )
        self.assertFalse(experiment["optimization_gain_claimed"])

    def test_actual_pi2_gpu_build_identity_and_original_source_are_bound(self):
        experiment = CURRENT["additional_experiments"][0]
        built = load("pi2-measured/candidate-build/result.json")
        actual = load("pi2-measured/candidate-build/candidate-inspect.json")
        self.assertEqual(built["image_id"], experiment["candidate_image_id"])
        self.assertEqual(actual["Id"], built["image_id"])
        self.assertEqual(load("pi2-measured/candidate/result.json")["image_id"], built["image_id"])
        source = (EVIDENCE / "pi2-measured/original-built-candidate.Dockerfile").read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), built["Dockerfile_sha256"])
        self.assertEqual(
            source, (EVIDENCE / "later-unmeasured-pi2/candidate.Dockerfile").read_bytes()
        )
        self.assertEqual(built["policy"]["changed_knobs"], ["--page-chunk-size"])
        self.assertEqual(
            actual["RootFS"]["Layers"], load("selected-image-inspect.json")[0]["RootFS"]["Layers"]
        )

    def test_pi3_actual_input_lengths_and_single_knob_build_are_bound(self):
        experiment = CURRENT["additional_experiments"][1]
        built = load("pi3-measured/candidate-build/result.json")
        actual = load("pi3-measured/candidate-build/candidate-inspect.json")
        self.assertEqual(built["image_id"], experiment["candidate_image_id"])
        self.assertEqual(actual["Id"], built["image_id"])
        self.assertEqual(built["policy"]["changed_knobs"], ["--prefill-chunk-size"])
        source = (EVIDENCE / "pi3-measured/original-built-candidate.Dockerfile").read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), built["Dockerfile_sha256"])
        self.assertEqual(source, (EVIDENCE / "pi3-measured/pi/candidate.Dockerfile").read_bytes())
        observed = {}
        for phase in ("control", "candidate", "selected-final"):
            rows = (EVIDENCE / "pi3-measured" / phase / "all-request-traces.jsonl")
            for line in rows.read_text().splitlines():
                trace = json.loads(line)
                observed.setdefault(trace["prompt_id"], set()).add(trace["usage_prompt_tokens"])
        expected = {
            "startup-probe": 14, "colors": 15, "capital": 23, "sum": 27,
            "haiku": 19, "explain": 22, "list": 22,
        }
        self.assertEqual(observed, {key: {value} for key, value in expected.items()})
        self.assertEqual(
            load("pi3-measured/reviewed-summary.json")["actual_usage_prompt_tokens"], expected
        )
        self.assertTrue(all(value < 256 for value in expected.values()))

    def test_pi4_raw_rejection_and_controller_repair_bind_actual_selected_graph(self):
        from abstract_harness import configuration_delivery

        prefix = "pi4-measured/"
        experiment = CURRENT["additional_experiments"][2]
        baseline = (EVIDENCE / prefix / "actual-source/baseline.Dockerfile").read_bytes()
        raw = (EVIDENCE / prefix / "pi/candidate.Dockerfile").read_bytes()
        corrected = (EVIDENCE / prefix / "pi/candidate.corrected.Dockerfile").read_bytes()
        self.assertEqual(baseline.count(b"--batch-size=16"), 1)
        self.assertEqual(corrected, baseline.replace(b"--batch-size=16", b"--batch-size=1"))
        self.assertEqual(
            corrected, (EVIDENCE / prefix / "original-built-candidate.Dockerfile").read_bytes()
        )
        with self.assertRaisesRegex(ValueError, "pinned original public FROM unchanged"):
            configuration_delivery._source_view(raw)
        self.assertEqual(
            load(prefix + "pi/raw-guard-result.json")["status"], "REJECTED_BEFORE_BUILD"
        )
        repair = load(prefix + "pi/controller-pin-repair.json")
        self.assertTrue(repair["not_unaltered_Pi_output"])
        self.assertFalse(repair["canonical_unattended_autocorrection_claimed"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), experiment["raw_model_Dockerfile_sha256"])
        self.assertEqual(
            hashlib.sha256(corrected).hexdigest(), experiment["candidate_Dockerfile_sha256"]
        )
        built = load(prefix + "candidate-build/result.json")
        self.assertEqual(built["policy"]["changed_knobs"], ["--batch-size"])
        self.assertEqual(built["Dockerfile_sha256"], hashlib.sha256(corrected).hexdigest())
        self.assertEqual(built["image_id"], experiment["candidate_image_id"])
        config_raw = (EVIDENCE / prefix / "selected-metadata/config.json").read_bytes()
        manifest_raw = (EVIDENCE / prefix / "selected-metadata/manifest.json").read_bytes()
        self.assertEqual(
            "sha256:" + hashlib.sha256(manifest_raw).hexdigest(), experiment["selected_image_id"]
        )
        manifest = json.loads(manifest_raw)
        config = json.loads(config_raw)
        self.assertEqual(
            manifest["config"]["digest"], "sha256:" + hashlib.sha256(config_raw).hexdigest()
        )
        self.assertEqual(len(manifest["layers"]), 23)
        self.assertEqual(
            manifest["layers"], load("pi2-measured/selected-metadata/manifest.json")["layers"]
        )
        self.assertEqual(config["os"], "linux")
        self.assertEqual(config["architecture"], "amd64")
        original = load("selected-image-inspect.json")[0]
        self.assertEqual(config["rootfs"]["diff_ids"], original["RootFS"]["Layers"])
        self.assertEqual(config["config"]["Entrypoint"], original["Config"]["Entrypoint"])
        self.assertEqual(config["config"]["Env"], original["Config"]["Env"])
        self.assertEqual(
            config["config"]["Cmd"],
            ["--batch-size=1" if value == "--batch-size=16" else value
             for value in original["Config"]["Cmd"]],
        )

    def test_followup_stock_pi_metadata_contains_only_permitted_event_fields(self):
        allowed = {
            "sequence", "event", "tool_call_id", "tool_name", "is_error",
            "argument_canonical_utf8_bytes", "argument_canonical_sha256",
            "allowed_relative_work_filenames",
        }
        for prefix, calls in (("pi3-measured/", 2), ("pi4-measured/", 3)):
            with self.subTest(prefix=prefix):
                metadata = load(prefix + "pi/tool-events.json")
                self.assertEqual(metadata["stock_cli"]["version"], "1.0.0")
                self.assertEqual(
                    metadata["launch_verification"]["Pi_distribution_files_verified"], 1243
                )
                self.assertEqual(metadata["effective_historical_wire_output_tokens"], 16384)
                events = metadata["tool_events"]
                self.assertTrue(all(set(event) <= allowed for event in events))
                starts = [event for event in events if event["event"] == "tool_execution_start"]
                ends = [event for event in events if event["event"] == "tool_execution_end"]
                self.assertEqual(len(starts), calls)
                self.assertEqual(len(ends), calls)
                self.assertEqual(
                    {event["tool_call_id"] for event in starts},
                    {event["tool_call_id"] for event in ends},
                )
                self.assertEqual(
                    metadata["assistant_stop_reason_metadata"][-1]["stop_reason"], "stop"
                )
                context = load(prefix + "pi/context/manifest.json")["files"]
                self.assertEqual(len(context), 6)
                for name, declared in context.items():
                    self.assertEqual(name, name.rsplit("/", 1)[-1])
                    raw = (EVIDENCE / prefix / "pi/context" / name).read_bytes()
                    self.assertEqual(len(raw), declared["bytes"])
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), declared["sha256"])

    def test_fixed_neighbor_policy_and_post_selection_final_bind_current_image(self):
        import runpy
        from dataclasses import asdict

        from abstract_harness import decision, measure

        prefix = "controller-neighbors/"
        root_choice = load(prefix + "final-selection.json")
        namespace = runpy.run_path(
            str(EVIDENCE / prefix / "portable-overlay/bounded_neighbor_candidates.py.txt")
        )
        supplement = load(prefix + "mixed-probe/RESULT.json")
        assessments = {}
        primary_total = 0
        for batch in (4, 8):
            folder = prefix + f"batch{batch}/"
            pair = load(folder + "paired-result.json")
            profile = load(folder + "actual-source/profile.json")
            golden = load(folder + "golden.json")
            samples = {}
            for phase in ("control", "candidate", "selected-final"):
                report = load(folder + phase + "/full-report.json")
                rows = (EVIDENCE / folder / phase / "all-request-traces.jsonl")
                durable = [json.loads(line) for line in rows.read_text().splitlines()]
                self.assertEqual(len(durable), 31)
                self.assertEqual(durable[1:], report["traces"])
                traces = [measure.RequestTrace(**trace) for trace in report["traces"]]
                self.assertEqual(Counter(t.prompt_id for t in traces), dict.fromkeys(golden, 5))
                self.assertEqual(
                    [asdict(g) for g in measure.correctness_gates(traces, profile, golden)],
                    report["gates"],
                )
                self.assertTrue(all(g["passed"] for g in report["gates"]))
                samples[phase] = load(folder + phase + "/samples.json")
                primary_total += len(traces)
            expected = decision.decide(
                samples["control"], samples["candidate"], gates_passed=True,
                min_effect=.03, min_samples=3, expected_samples=dict.fromkeys(golden, 5),
            )
            self.assertEqual(json.loads(json.dumps(asdict(expected))), pair["decision"])
            assessment = namespace["assess_mixed"](
                samples["control"], samples["candidate"], baseline_gates_passed=True,
                candidate_gates_passed=True, baseline_supplement=supplement["arms"]["baseline"],
                candidate_supplement=supplement["arms"][f"batch-{batch}"],
            )
            self.assertEqual(
                json.loads(json.dumps(assessment)), root_choice["assessments"][str(batch)]
            )
            assessments[batch] = assessment
        self.assertEqual(primary_total, 180)
        self.assertEqual(namespace["select_mixed"](assessments), root_choice["choice"])
        self.assertEqual(root_choice["selected_batch"], 4)
        self.assertEqual(root_choice["selected_image_id"], SELECTED)
        folder = prefix + "final-after-mixed/selected-final-after-mixed/"
        report = load(folder + "full-report.json")
        traces = [measure.RequestTrace(**trace) for trace in report["traces"]]
        self.assertEqual(len(traces), 30)
        self.assertEqual(
            [asdict(g) for g in measure.correctness_gates(traces, profile, golden)], report["gates"]
        )
        self.assertTrue(all(g["passed"] for g in report["gates"]))
        self.assertGreater(load(folder + "launch-start.json")["utc"], root_choice["observed_utc"])
        self.assertEqual(load(folder + "result.json")["image_id"], SELECTED)
        raw = (EVIDENCE / prefix / "batch4/selected-metadata/manifest.json").read_bytes()
        config_raw = (EVIDENCE / prefix / "batch4/selected-metadata/config.json").read_bytes()
        self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), SELECTED)
        self.assertEqual(
            "sha256:" + hashlib.sha256(config_raw).hexdigest(), CURRENT["delivery"]["config"]
        )
        self.assertEqual(json.loads(raw)["config"]["digest"], CURRENT["delivery"]["config"])
        self.assertEqual(
            json.loads(raw)["layers"],
            load("pi2-measured/selected-metadata/manifest.json")["layers"],
        )
        built_path = EVIDENCE / prefix / "batch4/original-built-candidate.Dockerfile"
        self.assertEqual(
            hashlib.sha256(built_path.read_bytes()).hexdigest(), CURRENT["build_recipe"]["sha256"]
        )
        self.assertEqual(CURRENT["evidence"]["primary_measured_requests"], 360 + primary_total + 30)
        self.assertEqual(CURRENT["evidence"]["supplemental_measured_requests"], 100)

    def test_public_replay_preparation_recreates_queue_and_both_start_states(self):
        import runpy
        import tempfile
        from pathlib import Path

        from abstract_harness.configuration_delivery import validate_patch_context
        from abstract_harness.proposers import (
            ProposerContext,
            ProposerError,
            ScriptedProposer,
            apply_patch,
        )

        folder = EVIDENCE / "controller-neighbors"
        frozen = folder / "frozen-before-measurements"
        helper = runpy.run_path(str(folder / "portable-overlay/prepare_neighbor_replay_v2.py.txt"))
        baseline = (EVIDENCE / "pi4-measured/actual-source/baseline.Dockerfile").read_bytes()
        target = Path("tasks/harness-design/Dockerfile")
        before = {p.name: p.read_bytes() for p in frozen.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper["prepare"](frozen, root / "queue", _paths.REPO_ROOT)
            generated = root / "queue/scripted-neighbor-queue-v2.json"
            published = folder / "portable-overlay/scripted-neighbor-queue-v2.json"
            self.assertEqual(
                generated.read_bytes(), published.read_bytes(),
            )
            original = root / "original"
            (original / target).parent.mkdir(parents=True)
            (original / target).write_bytes(baseline)
            for start in (1, 16):
                tree = root / f"start{start}"
                (tree / target).parent.mkdir(parents=True)
                (tree / target).write_bytes(
                    baseline.replace(b"--batch-size=16", f"--batch-size={start}".encode())
                )
                context = ProposerContext("", {}, [], tree, root, "llmd")
                if start == 1:
                    old = ScriptedProposer.from_file(frozen / "scripted-neighbor-queue.json")
                    with self.assertRaises(ProposerError):
                        old.propose(context)
                queue = ScriptedProposer.from_file(generated)
                for value in (4, 8):
                    proposal = queue.propose(context)
                    ok, reason = apply_patch(proposal.patch, tree)
                    self.assertTrue(ok, reason)
                    self.assertEqual(
                        (tree / target).read_bytes(),
                        (frozen / f"batch{value}.Dockerfile").read_bytes(),
                    )
                    self.assertEqual(
                        validate_patch_context(original, tree)["changed_knobs"], ["--batch-size"]
                    )
                self.assertIsNone(queue.propose(context))
        self.assertEqual({p.name: p.read_bytes() for p in frozen.iterdir() if p.is_file()}, before)


if __name__ == "__main__":
    unittest.main()
