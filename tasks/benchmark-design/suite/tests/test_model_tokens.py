"""Optional real pinned tokenizer integration; no weights, GPU or model inference."""

import asyncio
import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import evaluate as ev_mod
import httpx
from bench.gates import judge, token_times
from bench.model_tokens import ModelTokenizer, TokenizerError, _nfc_boundaries, model_spec
from bench.plan import load_plan
from runner.run import RequestMeta, Runner

from tests.helpers import SUITE, clean_cell, make_record, make_run, plan, write_events
from tests.test_mock_server import runner_args


class OfflineBoundaryTests(unittest.TestCase):
    def test_model_roles_match_protected_pins(self):
        for role in ("target", "smoke"):
            self.assertEqual(len(model_spec(role)["revision"]), 40)
        with self.assertRaises(TokenizerError):
            model_spec("untrusted-model")

    def test_real_runner_refuses_regex_fallback_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(TokenizerError, "requires --tokenizer-snapshot"):
                Runner(runner_args(out=tmp, assume_mock=False), plan())

    def test_nfc_maps_combining_marks_and_hangul_to_complete_original_cluster(self):
        normalized, ends = _nfc_boundaries("e\u0301 \u1100\u1161\u11a8")
        self.assertEqual(normalized, "\u00e9 \uac01")
        self.assertEqual(ends, [2, 3, 6])


class PinnedTokenizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot_root = os.environ.get("BENCHMARK_TOKENIZER_SNAPSHOTS")
        if not snapshot_root:
            raise unittest.SkipTest(
                "set BENCHMARK_TOKENIZER_SNAPSHOTS for downloaded pinned assets"
            )
        cls.root = Path(snapshot_root)
        cls.adapters = {role: ModelTokenizer(cls.root / role, role) for role in ("target", "smoke")}

    def test_efficient_integer_sequences_fit_the_fixed_cap_with_token_headroom(self):
        workload = load_plan(SUITE / "workload.efficient.json")
        for source, cfg in workload["prompt_sources"].items():
            if cfg["kind"] != "integer_sequence":
                continue
            answer = "\n".join(map(str, range(1, cfg["sequence_end"]["max"] + 1))) + "\n"
            # Canonical complete output plus eight tokens of termination headroom.
            # This is a feasibility check, not proof of model correctness/style.
            with self.subTest(source=source):
                self.assertLessEqual(self.adapters["target"].count(answer) + 8, cfg["max_tokens"])

    def test_pinned_known_ids_and_multilingual_normalization(self):
        vectors = {
            "target": ([14556, 1814], [247359, 169171, 3825]),
            "smoke": ([14990, 1879], [101059, 102819, 145080, 3958]),
        }
        text = "\u65e5\u672c\u8a9e\U0001f642 e\u0301"
        for role, adapter in self.adapters.items():
            with self.subTest(role=role):
                self.assertEqual(adapter.backend.encode("hello world").ids, vectors[role][0])
                self.assertEqual(adapter.backend.encode(text).ids, vectors[role][1])
                self.assertEqual(adapter.count(text), len(vectors[role][1]))
                self.assertEqual(adapter.end_offsets(text)[-1], len(text))
                self.assertEqual(adapter.count(""), 0)

    def test_bpe_cross_delta_merges_and_unicode_wait_for_observed_completion(self):
        for adapter in self.adapters.values():
            times, count = token_times([("hel", 11.0), ("lo", 12.0)], 10.0, adapter)
            self.assertEqual((times, count), ([2.0], 1))
            self.assertGreater(adapter.count("hel") + adapter.count("lo"), count)
            times, count = token_times([("e", 11.0), ("\u0301", 12.0)], 10.0, adapter)
            self.assertEqual((times, count), ([2.0], 1))
            text = "\U0001f9d1\u200d\U0001f4bb\u65e5\u672c\u8a9e"
            deltas = [(char, 11.0 + i) for i, char in enumerate(text)]
            times, count = token_times(deltas, 10.0, adapter)
            self.assertEqual(count, adapter.count(text))
            self.assertEqual(times, sorted(times))
            self.assertTrue(all(t in [at - 10.0 for _, at in deltas] for t in times))

    def test_wrong_snapshot_or_same_sized_corruption_fails_closed(self):
        with self.assertRaises(TokenizerError):
            ModelTokenizer(self.root / "target", "smoke")
        with tempfile.TemporaryDirectory() as tmp:
            for file in ("tokenizer.json", "tokenizer_config.json"):
                shutil.copyfile(self.root / "target" / file, Path(tmp) / file)
            p = Path(tmp) / "tokenizer_config.json"
            raw = bytearray(p.read_bytes())
            raw[20] ^= 1
            p.write_bytes(raw)
            with self.assertRaisesRegex(TokenizerError, "SHA-256"):
                ModelTokenizer(Path(tmp), "target")

    def test_gate_reconstructs_counts_and_rejects_identity_or_count_mismatch(self):
        adapter = self.adapters["target"]
        record = make_record(plan())
        record["client_tokens"].update(
            tokenizer=adapter.identifier, content=adapter.count(record["text"]["content"])
        )
        self.assertEqual(judge(record, plan(), [], adapter).category, "completed")
        bad = copy.deepcopy(record)
        bad["client_tokens"]["content"] += 1
        self.assertEqual(judge(bad, plan(), [], adapter).category, "evidence_invalid")
        self.assertEqual(judge(record, plan(), []).category, "evidence_invalid")

    def test_evaluator_requires_matching_runtime_snapshot_and_model(self):
        adapter = self.adapters["target"]
        records = clean_cell(plan(), "chat_short_open", 12)
        run = make_run(records)
        run["engine"].update(kind="pinned-llmd", lock_model=adapter.model)
        run["tokenizer"] = adapter.identity
        for record in records:
            record["client_tokens"].update(
                tokenizer=adapter.identifier, content=adapter.count(record["text"]["content"])
            )
        with tempfile.TemporaryDirectory() as tmp:
            write_events(Path(tmp), run, records)
            self.assertEqual(
                ev_mod.evaluate(plan(), Path(tmp), "native", tokenizer=adapter)[0]["status"],
                "valid",
            )
            self.assertEqual(ev_mod.evaluate(plan(), Path(tmp), "native")[0]["status"], "invalid")
            wrong = self.adapters["smoke"]
            self.assertEqual(
                ev_mod.evaluate(plan(), Path(tmp), "native", tokenizer=wrong)[0]["status"],
                "invalid",
            )
            run["engine"]["lock_model"] = wrong.model
            run["tokenizer"] = wrong.identity
            write_events(Path(tmp), run, records)
            report = ev_mod.evaluate(plan(), Path(tmp), "native", tokenizer=wrong)[0]
            self.assertIn("tokenizer model role differs", " ".join(report["reasons"]))

    def test_runner_preserves_utf8_split_across_network_chunks(self):
        """Exercise actual runner decoding with every byte as a separate transport chunk."""
        adapter = self.adapters["target"]
        record = make_record(plan(), text="\u65e5\u672c\u8a9e\U0001f642 e\u0301")
        wire = "".join("data: " + e["raw"] + "\n\n" for e in record["events"]).encode("utf-8")

        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                for byte in wire:
                    yield bytes([byte])

        async def exchange(tmp):
            args = runner_args(out=tmp, tokenizer_snapshot=self.root / "target")
            runner = Runner(args, plan())
            transport = httpx.MockTransport(
                lambda _: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Bytes()
                )
            )
            from bench.prompts import make_prompt

            spec = make_prompt(plan(), "chat_short", "test", 0, 0)
            async with httpx.AsyncClient(transport=transport) as client:
                return await runner.execute(
                    client, spec, RequestMeta("utf8", None, "startup_probe", "startup", 0)
                )

        with tempfile.TemporaryDirectory() as tmp:
            captured = asyncio.run(exchange(tmp))
        self.assertEqual(captured["text"]["content"], record["text"]["content"])
        self.assertEqual(
            captured["client_tokens"]["content"], adapter.count(record["text"]["content"])
        )
        self.assertEqual(captured["client_tokens"]["resolution"], "delta")

    def test_unencodable_stream_is_preserved_as_invalid_evidence_without_fallback(self):
        adapter = self.adapters["target"]
        record = make_record(plan())
        original = "".join("data: " + e["raw"] + "\n\n" for e in record["events"]).encode()

        async def exchange(wire, tmp):
            runner = Runner(runner_args(out=tmp, tokenizer_snapshot=self.root / "target"), plan())
            transport = httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=wire,
                )
            )
            from bench.prompts import make_prompt

            spec = make_prompt(plan(), "chat_short", "test", 0, 0)
            async with httpx.AsyncClient(transport=transport) as client:
                captured = await runner.execute(
                    client, spec, RequestMeta("bad-utf8", None, "startup_probe", "startup", 0)
                )
            path = Path(tmp) / "bad-record.jsonl"
            with path.open("w", encoding="utf-8") as handle:
                runner._requests_fh = handle
                runner._write_request(captured)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), captured)
            return captured

        for wire in (
            original.replace(b"CODE-", b"CODE-\xff", 1),
            original.replace(b"latency", b"latency" + "\u0301".encode() * 4097, 1),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                captured = asyncio.run(exchange(wire, tmp))
            self.assertTrue(captured["client_tokens"]["count_error"])
            self.assertEqual(captured["client_tokens"]["content"], 0)
            self.assertEqual(judge(captured, plan(), [], adapter).category, "evidence_invalid")


if __name__ == "__main__":
    unittest.main()
