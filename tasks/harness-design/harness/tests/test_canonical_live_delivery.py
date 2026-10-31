"""Replay actual canonical GPU evidence and prevent attribution/provenance regressions."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import unittest
import zipfile
from collections import Counter
from dataclasses import asdict

from abstract_harness import decision, measure

from . import _paths

TASK = _paths.HARNESS_DIR.parent
CURRENT = json.loads((TASK / "submission.json").read_text(encoding="utf-8"))
EVIDENCE = _paths.REPO_ROOT / CURRENT["evidence"]["directory"]


def load(name):
    return json.loads((EVIDENCE / name).read_bytes())


class CanonicalLiveDeliveryTests(unittest.TestCase):
    def test_each_original_worker_journal_matches_report_and_replayed_gates(self):
        profile = load("actual-source/development-benchmark.llmd.json")
        golden = load("golden.json")
        count = 0
        for phase in ("baseline", "control", "candidate", "selected-final"):
            with self.subTest(phase=phase):
                report = load(phase + "/full-report.json")
                events = [
                    json.loads(line)
                    for line in (EVIDENCE / phase / "request-traces.jsonl")
                    .read_bytes()
                    .splitlines()
                ]
                self.assertEqual(len(events), 30)
                self.assertTrue(all(row["event"] == "benchmark_request" for row in events))
                self.assertEqual([row["trace"] for row in events], report["traces"])
                traces = [measure.RequestTrace(**row["trace"]) for row in events]
                self.assertEqual(Counter(t.prompt_id for t in traces), dict.fromkeys(golden, 5))
                # The first baseline establishes the golden set; it cannot compare to itself yet.
                reference = None if phase == "baseline" else golden
                self.assertEqual(
                    [asdict(g) for g in measure.correctness_gates(traces, profile, reference)],
                    report["gates"],
                )
                if phase == "baseline":
                    self.assertEqual({t.prompt_id: t.text for t in traces}, golden)
                self.assertEqual(len(report["gates"]), 6)
                self.assertTrue(all(g["passed"] for g in report["gates"]))
                probes = [
                    json.loads(line)
                    for line in (EVIDENCE / phase / "startup-traces.jsonl")
                    .read_bytes()
                    .splitlines()
                ]
                self.assertEqual(len(probes), 1)
                self.assertEqual(probes[0]["event"], "startup_probe")
                self.assertTrue(probes[0]["trace"]["ok"])
                self.assertEqual(load(phase + "/startup-result.json")[0], report["startup_s"])
                count += len(traces)
        self.assertEqual(count, 120)

    def test_replayed_pair_keeps_ftgr_and_does_not_claim_ttft_or_startup(self):
        expected = decision.decide(
            load("control/samples.json"),
            load("candidate/samples.json"),
            gates_passed=True,
            min_effect=0.03,
            min_samples=3,
            expected_samples=dict.fromkeys(load("golden.json"), 5),
        )
        self.assertEqual(json.loads(json.dumps(asdict(expected))), load("decision.json"))
        self.assertTrue(expected.kept)
        self.assertEqual(expected.comparison("ftgr_tok_s").verdict, "improved")
        self.assertEqual(expected.comparison("ttft_s").verdict, "no_change")
        self.assertEqual(expected.comparison("startup_s").verdict, "insufficient")
        summary = load("reviewed-summary.json")
        self.assertFalse(summary["TTFT_improvement_supported"])
        self.assertFalse(summary["startup_statistical_improvement_supported"])
        self.assertFalse(summary["official_grade_claimed"])

    def test_main_log_only_normalizes_public_paths_and_preserves_invalid_history(self):
        raw = (EVIDENCE / "raw-results.tsv").read_bytes()
        public = (_paths.HARNESS_DIR / "results.tsv").read_bytes()
        mapping = load("result-path-map.json")
        self.assertEqual(hashlib.sha256(raw).hexdigest(), mapping["raw_sha256"])
        self.assertEqual(hashlib.sha256(public).hexdigest(), mapping["normalized_sha256"])
        old_rows = list(csv.DictReader(io.StringIO(raw.decode()), delimiter="\t"))
        new_rows = list(csv.DictReader(io.StringIO(public.decode()), delimiter="\t"))
        self.assertEqual(len(old_rows), 2)
        self.assertEqual(len(new_rows), 2)
        for old, new in zip(old_rows, new_rows, strict=True):
            for key in old:
                if key in ("development_report", "patch_path") and old[key]:
                    self.assertEqual(new[key], mapping["paths"][old[key]])
                    self.assertTrue((_paths.REPO_ROOT / new[key]).is_file())
                else:
                    self.assertEqual(old[key], new[key])
        history = _paths.HARNESS_DIR / "reports/history-cpu-standin-20261024/results.tsv"
        self.assertEqual(
            hashlib.sha256(history.read_bytes()).hexdigest(),
            "a063f17a497469c426f8bbb19e888918bca32582ca502b8f63da03a524ad4b3c",
        )
        self.assertEqual(
            len(list(csv.DictReader(io.StringIO(history.read_text()), delimiter="\t"))), 14
        )

    def test_image_metadata_and_model_patch_preserve_distinct_identities(self):
        summary = load("reviewed-summary.json")
        raw = (EVIDENCE / "selected-metadata/manifest.json").read_bytes()
        config = (EVIDENCE / "selected-metadata/config.json").read_bytes()
        self.assertEqual(
            "sha256:" + hashlib.sha256(raw).hexdigest(), summary["local_selected_platform_manifest"]
        )
        self.assertEqual("sha256:" + hashlib.sha256(config).hexdigest(), summary["selected_config"])
        manifest = json.loads(raw)
        self.assertEqual(manifest["config"]["digest"], summary["selected_config"])
        self.assertEqual(len(manifest["layers"]), 23)
        self.assertNotEqual(summary["local_selected_platform_manifest"], CURRENT["image_digest"])
        recipe = (EVIDENCE / "original-built-candidate.Dockerfile").read_text()
        self.assertIn("--batch-size=1", recipe)
        self.assertNotIn("--batch-size=16", recipe)
        cpu = load("cpu-provenance.json")
        self.assertEqual(
            hashlib.sha256((EVIDENCE / "proposal.patch").read_bytes()).hexdigest(),
            cpu["model_patch_sha256"],
        )

    def test_current_attribution_keeps_new_canonical_cycle_and_prior_recommendation_separate(self):
        summary = load("reviewed-summary.json")
        cpu = load("cpu-provenance.json")
        self.assertEqual(
            CURRENT["source"]["canonical_runtime_commit"],
            "5c439274e2d3744da68b310c769c96b0c78bfb92",
        )
        self.assertTrue(CURRENT["agent_harness"]["canonical_unattended_GPU_loop_executed"])
        self.assertFalse(CURRENT["agent_harness"]["one_continuous_Pi_and_GPU_process_claimed"])
        self.assertEqual(summary["new_GPU_model_calls"], 0)
        self.assertEqual(cpu["provider_requests"], 1)
        self.assertTrue(cpu["original_provider_SDK_JSON_patch_exact"])
        self.assertEqual(cpu["execution_policy"]["tools"], [])
        self.assertEqual(cpu["provider_response_format"], {"type": "json_object"})
        self.assertEqual(cpu["known_usage_priced_upper_USD"], "0.0144352")
        self.assertTrue(cpu["metadata_filename_alias_fix"]["no_model_text_or_patch_change"])
        self.assertTrue(CURRENT["delivery"]["anonymous_docker_pull_and_help_verified"])
        self.assertFalse(CURRENT["recommended_image_selection"]["canonical_model_chose4_claimed"])
        prior = _paths.REPO_ROOT / CURRENT["historical_snapshots"][0]["path"]
        self.assertEqual(
            hashlib.sha256(prior.read_bytes()).hexdigest(),
            CURRENT["historical_snapshots"][0]["sha256"],
        )
        self.assertEqual(json.loads(prior.read_bytes())["image"], CURRENT["image"])
        self.assertEqual(CURRENT["source"]["branch"], "codex/task2-real-run-final")
        for path in (
            CURRENT["source"]["dockerfile"],
            CURRENT["source"]["provenance_path"],
            CURRENT["agent_harness"]["entrypoint"],
            CURRENT["agent_harness"]["actual_phase_entrypoint_evidence"],
            CURRENT["agent_harness"]["provenance_path"],
            CURRENT["integrity"]["report"],
            CURRENT["evidence"]["report"],
        ):
            self.assertTrue((_paths.REPO_ROOT / path).is_file(), path)
        self.assertIn(CURRENT["source"]["dockerfile"], CURRENT["source"]["build_command"])
        self.assertIn("UNAVAILABLE_IN_DRAFT", CURRENT["source"]["engine_source_revision"])
        self.assertIn("--batch-size=4", CURRENT["runtime"]["args"])

    def test_public_inventory_and_source_provenance_are_hash_bound(self):
        for name, expected in load("inventory.json")["files"].items():
            with self.subTest(name=name):
                raw = (EVIDENCE / name).read_bytes()
                self.assertEqual(len(raw), expected["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected["sha256"])
        for name, provenance in load("provenance-map.json")["files"].items():
            self.assertEqual(
                hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(),
                provenance["public_sha256"],
            )
            if provenance["transformation"] == "verbatim":
                self.assertEqual(provenance["public_sha256"], provenance["source_sha256"])

    def test_experimental_publication_verifies_exact_graph_without_replacing_recommendation(self):
        delivery = load("publication/VERIFIED.json")
        review_raw = (EVIDENCE / "publication/INDEPENDENT-REVIEW.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(review_raw).hexdigest(), delivery["independent_review_sha256"]
        )
        self.assertEqual(delivery["publication_head"], "41456da6d144abea96aada865703286a02b935f8")
        self.assertEqual(delivery["workflow_run_id"], 41051877610)
        self.assertTrue(delivery["registry_publication_verified"])
        self.assertTrue(delivery["anonymous_complete_graph_verified"])
        self.assertTrue(delivery["anonymous_docker_pull_and_help_verified"])
        self.assertTrue(delivery["owned_help_container_absence_verified"])
        self.assertFalse(delivery["recommended_global_batch4_replaced"])
        manifest_raw = (EVIDENCE / "selected-metadata/manifest.json").read_bytes()
        config_raw = (EVIDENCE / "selected-metadata/config.json").read_bytes()
        manifest = json.loads(manifest_raw)
        self.assertEqual(
            "sha256:" + hashlib.sha256(manifest_raw).hexdigest(), delivery["platform_manifest"]
        )
        self.assertEqual("sha256:" + hashlib.sha256(config_raw).hexdigest(), delivery["config"])
        self.assertEqual(delivery["anonymous_graph_objects"], 25)
        self.assertEqual(delivery["anonymous_graph_layers"], 23)
        self.assertEqual(
            len(manifest_raw) + len(config_raw) + sum(row["size"] for row in manifest["layers"]),
            delivery["anonymous_graph_bytes"],
        )
        review = json.loads(review_raw)
        closure = load("publication/FINAL-CODE-AND-DELIVERY-REVIEW.json")
        self.assertEqual(closure["actual_publication_head"], delivery["publication_head"])
        self.assertTrue(closure["generic_CI"]["all_three_success"])
        self.assertEqual(closure["Greptile"]["conclusion"], "success")
        self.assertTrue(
            closure["post_publication_activation_gate"][
                "all_three_publication_jobs_skipped_without_steps"
            ]
        )
        pins = {
            "publish-receipt.json": "publication_receipt_sha256",
            "anonymous-receipt.json": "anonymous_receipt_sha256",
            "docker-receipt.json": "Docker_receipt_sha256",
        }
        for name, expected in delivery["archives"].items():
            path = EVIDENCE / "publication" / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected["sha256"])
            with zipfile.ZipFile(path) as archive:
                self.assertIsNone(archive.testzip())
                primary = archive.read(expected["primary_receipt_member"])
            self.assertEqual(
                primary,
                (EVIDENCE / "publication" / expected["public_primary_receipt"]).read_bytes(),
            )
            self.assertEqual(
                hashlib.sha256(primary).hexdigest(),
                review[pins[expected["public_primary_receipt"]]],
            )
            artifact = {
                "publish-receipt.json": "publication",
                "anonymous-receipt.json": "anonymous",
                "docker-receipt.json": "Docker",
            }
            self.assertEqual(
                expected["sha256"],
                closure["artifact_zip_sha256"][artifact[expected["public_primary_receipt"]]],
            )
        self.assertEqual(CURRENT["experimental_image_delivery"]["reference"], delivery["reference"])
        self.assertNotEqual(CURRENT["image_digest"], delivery["platform_manifest"])
        self.assertTrue(
            CURRENT["image_digest"].endswith(
                "84d13e8e4f6777ec4522d701d86e16181c0b170602a7e81db17dab7df525cabb"
            )
        )


if __name__ == "__main__":
    unittest.main()
