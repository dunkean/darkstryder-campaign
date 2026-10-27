"""Offline regression fixtures. No real corpus, sockets, downloads, model or GPU."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "content" / "transcriptions"))
import phase4_merge as corpus
import phase4_pipeline as pipeline


class Phase4(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "transcriptions"
        self.root.mkdir()
        self.out = self.root / "_extraction"
        self.work = self.out / "phase4"
        self.args = pipeline.make_parser().parse_args(["candidates", "--root", str(self.root)])
        self.args.writer_model = "offline-writer"
        self.sources = []
        self.documents = {}

    def fixture(self, did: str, source_hex: str, name: str = "Sarne", aliases: list[str] | None = None,
                category: str = "character", format: str = "pdf", body: str | None = None) -> Path:
        sid = "src-" + source_hex * 16
        source = {"id": sid, "name": f"Original {source_hex}", "format": format, "status": "complete",
                  "collection": "addons" if format == "html" else "darkstryder",
                  "locator": {"root": "drive", "path": f"originals/{sid}.{format}"},
                  "unitLabel": "page PDF" if format == "pdf" else "segment documentaire"}
        self.sources.append(source)
        pipeline.atomic_json(self.root / "index.json", {"sources": self.sources})
        original = f"<!-- source: {sid}; pdf_page: 1 -->\n{name} commands the fleet. Strength 3D.\n"
        pipeline.atomic_text(self.root / sid / "pages" / "0001.md", original)
        pipeline.atomic_json(self.root / sid / "transcription.json", {
            "sourceId": sid, "sourceFormat": format, "sha256": source_hex * 64,
            "locator": source["locator"], "collections": [source["collection"]],
            "evidenceProvenance": "campaign-addition-unreviewed" if format == "html" else "published-source",
            "fileHashes": {"pages/0001.md": pipeline.file_hash(self.root / sid / "pages" / "0001.md")}})
        pipeline.atomic_json(self.root / sid / "manifest.json", {"warnings": [{"kind": "fixture-warning"}]})
        meta = {"id": did, "source_id": sid, "source_name": source["name"], "name": name,
                "aliases": aliases or [], "category": category, "profile_pages": [1], "role_pages": []}
        text = "---\n" + "\n".join(f"{key}: {json.dumps(value)}" for key, value in meta.items())
        text += f"\n---\n\n# {name}\n\n## Identity\n\n" + (body or f"{name} commands the fleet (p. 1). Strength 3D (p. 1).") + "\n"
        path = self.out / "documents" / sid / category / f"subject--{did}.md"
        pipeline.atomic_text(path, text)
        self.documents[did] = path
        return path

    def index(self) -> corpus.SearchIndex:
        corpus.build_index(self.root, self.out)
        index = corpus.SearchIndex(self.work / "index.sqlite")
        self.addCleanup(index.close)
        return index

    def setup_pairs(self, index: corpus.SearchIndex) -> None:
        pipeline.candidates(index, self.work, self.args)

    def set_decisions(self, verdicts: dict[tuple[str, str], str]) -> None:
        db = pipeline.state_db(self.work)
        try:
            pipeline.put_meta(db, "classifier", pipeline.digest(pipeline.classifier_spec(self.args)))
            for (a, b), label in verdicts.items():
                db.execute("UPDATE pairs SET decision=?,cache_key=? WHERE a=? AND b=?",
                           (label, label + a + b, *sorted([a, b])))
            db.commit()
        finally:
            db.close()

    def fake_inference(self, route: str, payload: dict) -> dict:
        if route == "/classifier":
            for question in payload["questions"].values():
                self.assertEqual(set(question["criteria"]), set(pipeline.LABELS))
            return {"model": self.args.classifier_model, "answers": {key: {
                "choice": "same_entity", "probabilities": {"same_entity": .98, "different_entity": .01, "uncertain": .01}}
                for key in payload["questions"]}}
        inputs = json.loads(payload["messages"][1]["content"])["inputs"]
        refs = [item["evidence_id"] for item in inputs if item.get("kind") == "original"]
        if not refs:
            refs = sorted({ref for item in inputs for ref in item["evidence_ids"]})
        value = {"paragraphs": [{"section": "Identité", "text": "Sarne commande la flotte. Force 3D.", "evidence_ids": refs}]}
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}

    def test_cpu_retrieval_exact_alias_cross_category_and_resume(self) -> None:
        self.fixture("a", "a", aliases=["Moff Sarne"])
        self.fixture("b", "b", name="Moff Sarne", category="organization")
        index = self.index()
        with patch.object(pipeline.LocalHTTP, "post", side_effect=AssertionError("network forbidden")):
            self.setup_pairs(index)
            db = pipeline.state_db(self.work)
            row = db.execute("SELECT * FROM pairs").fetchone()
            self.assertEqual((row["a"], row["b"]), ("a", "b"))
            self.assertIn("exact_name_or_alias", row["reasons"])
            db.close()
            self.set_decisions({("a", "b"): "same_entity"})
            pipeline.candidates(index, self.work, self.args)
            db = pipeline.state_db(self.work)
            self.assertEqual(db.execute("SELECT decision FROM pairs").fetchone()[0], "same_entity")
            db.close()

    def test_groups_require_direct_anchor_no_transitive_chain_and_no_overlap(self) -> None:
        for did in ["a", "b", "c"]:
            self.fixture(did, did)
        index = self.index()
        self.setup_pairs(index)
        self.set_decisions({("a", "b"): "same_entity", ("b", "c"): "same_entity", ("a", "c"): "different_entity"})
        pipeline.groups(index, self.work, self.args)
        grouping = pipeline.load(self.work / "groups.json")
        self.assertEqual([g["document_ids"] for g in grouping["groups"]], [["a", "b"], ["c"]])
        ids = [did for group in grouping["groups"] for did in group["document_ids"]]
        self.assertEqual(len(ids), len(set(ids)))
        first_id = grouping["groups"][0]["corpus_id"]
        pipeline.groups(index, self.work, self.args)
        self.assertEqual(pipeline.load(self.work / "groups.json")["groups"][0]["corpus_id"], first_id)

    def test_member_disagreement_blocks_positive_anchor(self) -> None:
        for did in ["a", "b", "c"]:
            self.fixture(did, did)
        index = self.index()
        self.setup_pairs(index)
        self.set_decisions({("a", "b"): "same_entity", ("a", "c"): "same_entity", ("b", "c"): "uncertain"})
        pipeline.groups(index, self.work, self.args)
        grouping = pipeline.load(self.work / "groups.json")
        self.assertEqual(grouping["groups"][0]["document_ids"], ["a", "b"])
        self.assertEqual(grouping["conflicts"][0]["candidate_id"], "c")

    def test_name_trigrams_find_ocr_variant_without_shared_words(self) -> None:
        self.fixture("a", "a", name="Akenseh", body="Alpha (p. 1).")
        self.fixture("b", "b", name="Akenesh", body="Beta (p. 1).")
        index = self.index()
        self.setup_pairs(index)
        db = pipeline.state_db(self.work)
        try:
            self.assertIn("name_trigrams", db.execute("SELECT reasons FROM pairs").fetchone()[0])
        finally:
            db.close()

    def test_classifier_batches_share_anchor_and_keep_independent_pair_caches(self) -> None:
        for did in ["a", "b", "c"]:
            self.fixture(did, did)
        index = self.index()
        self.setup_pairs(index)
        with patch.object(pipeline.LocalHTTP, "post", side_effect=self.fake_inference) as post:
            pipeline.classify(index, self.work, self.args)
            self.assertEqual(post.call_count, 2)  # a->[b,c], b->[c], not three independent prefills
            first_payload = post.call_args_list[0].args[1]
            self.assertEqual(first_payload["state"]["TARGET"]["document_id"], "a")
            self.assertEqual(set(first_payload["questions"]), {"b", "c"})
            self.assertEqual(len(list((self.work / "classification").rglob("*.json"))), 3)

    def test_positive_abbreviated_record_is_reviewed_not_auto_merged(self) -> None:
        self.fixture("a", "a", body="Sarne " * 3000 + " (p. 1).")
        self.fixture("b", "b")
        self.args.classifier_chars = 1000
        index = self.index()
        self.setup_pairs(index)
        with patch.object(pipeline.LocalHTTP, "post", side_effect=self.fake_inference):
            pipeline.classify(index, self.work, self.args)
        db = pipeline.state_db(self.work)
        try:
            row = db.execute("SELECT decision,response_json FROM pairs").fetchone()
            self.assertEqual(row[0], "uncertain")
            self.assertTrue(json.loads(row[1])["verdict"]["budget_limited"])
        finally:
            db.close()

    def test_changed_original_invalidates_writer_cache_and_stale_phase3_is_refused(self) -> None:
        self.fixture("a", "a")
        index = self.index()
        self.setup_pairs(index)
        self.set_decisions({})
        pipeline.groups(index, self.work, self.args)
        with patch.object(pipeline.LocalHTTP, "post", side_effect=self.fake_inference) as post:
            pipeline.aggregate(index, self.root, self.out, self.work, self.args)
            count = post.call_count
            original = self.root / ("src-" + "a" * 16) / "pages" / "0001.md"
            original.write_bytes(original.read_bytes() + b"\nA corrected original fact.\n")
            pipeline.aggregate(index, self.root, self.out, self.work, self.args)
            self.assertGreater(post.call_count, count)
            count = post.call_count
            document = self.documents["a"]
            document.write_bytes(document.read_bytes() + b"\nChanged phase-3 record.\n")
            with self.assertRaises(RuntimeError):
                pipeline.aggregate(index, self.root, self.out, self.work, self.args)
            self.assertEqual(post.call_count, count)

    def test_pending_pairs_prevent_groups_and_threshold_creates_review(self) -> None:
        self.fixture("a", "a")
        self.fixture("b", "b")
        index = self.index()
        self.setup_pairs(index)
        self.set_decisions({})
        with self.assertRaisesRegex(ValueError, "pending"):
            pipeline.groups(index, self.work, self.args)
        response = self.fake_inference("/classifier", pipeline.classify_request(index.document("a"), index.document("b"), self.args))
        response["answers"]["identity"]["probabilities"] = {"same_entity": .85, "different_entity": .10, "uncertain": .05}
        self.assertEqual(pipeline.parse_verdict(response, self.args)["decision"], "uncertain")
        response["answers"]["identity"]["probabilities"]["same_entity"] = float("nan")
        with self.assertRaises(ValueError):
            pipeline.parse_verdict(response, self.args)

    def test_end_to_end_fake_classifier_writer_provenance_resume_and_local_edits(self) -> None:
        self.fixture("a", "a")
        self.fixture("b", "b", format="html")
        index = self.index()
        self.setup_pairs(index)
        with patch.object(pipeline.LocalHTTP, "post", side_effect=self.fake_inference) as post:
            pipeline.classify(index, self.work, self.args)
            calls = post.call_count
            pipeline.classify(index, self.work, self.args)
            self.assertEqual(post.call_count, calls)
            pipeline.groups(index, self.work, self.args)
            pipeline.aggregate(index, self.root, self.out, self.work, self.args)
            grouping = pipeline.load(self.work / "groups.json")
            folder = self.out / "corpus-v2" / grouping["groups"][0]["corpus_id"]
            text = (folder / "document.md").read_text(encoding="utf-8")
            self.assertIn("[^s1] [^s2]", text)
            self.assertIn("(pdf; p. 1)", text)
            self.assertIn("(html; segment documentaire 1)", text)
            self.assertIn("campaign-addition-unreviewed", text)
            prov = pipeline.load(folder / "provenance.json")
            for item in prov["evidence"]:
                original = (self.root / item["markdown_path"]).read_text(encoding="utf-8")
                self.assertIn(item["excerpt"], original)
                self.assertEqual(pipeline.file_hash(self.root / item["markdown_path"]), item["markdown_sha256"])
                self.assertTrue(item["source"]["original_sha256"])
                self.assertEqual(item["source"]["warnings"][0]["kind"], "fixture-warning")
            calls = post.call_count
            pipeline.aggregate(index, self.root, self.out, self.work, self.args)
            self.assertEqual(post.call_count, calls)
            pipeline.atomic_text(folder / "document.md", text + "\nPersonal correction\n")
            with self.assertRaisesRegex(RuntimeError, "failed"):
                pipeline.aggregate(index, self.root, self.out, self.work, self.args)
            self.assertIn("Personal correction", (folder / "document.md").read_text())
            self.assertEqual(post.call_count, calls)

    def test_changed_document_invalidates_pair_and_keeps_original_id(self) -> None:
        self.fixture("a", "a")
        b = self.fixture("b", "b")
        index = self.index()
        self.setup_pairs(index)
        with patch.object(pipeline.LocalHTTP, "post", side_effect=self.fake_inference):
            pipeline.classify(index, self.work, self.args)
        index.close()
        b.write_text(b.read_text(encoding="utf-8") + "\nNew fact (p. 1).", encoding="utf-8")
        index = self.index()
        with self.assertRaisesRegex(ValueError, "stale"):
            pipeline.groups(index, self.work, self.args)
        self.setup_pairs(index)
        db = pipeline.state_db(self.work)
        self.assertIsNone(db.execute("SELECT decision FROM pairs").fetchone()[0])
        db.close()
        with patch.object(pipeline.LocalHTTP, "post", side_effect=self.fake_inference) as post:
            pipeline.classify(index, self.work, self.args)
            self.assertEqual(post.call_count, 1)

    def test_changed_original_is_used_without_overwrite_and_marked_corrected(self) -> None:
        self.fixture("a", "a")
        index = self.index()
        original = self.root / ("src-" + "a" * 16) / "pages" / "0001.md"
        original.write_text("Personal corrected transcript. Sarne commands the fleet.", encoding="utf-8")
        evidence, _ = pipeline.original_evidence(index, self.root, ["a"])
        self.assertTrue(evidence[0]["locally_corrected"])
        self.assertEqual(evidence[0]["excerpt"], original.read_text())

    def test_refuses_unsourced_unknown_and_lost_citations(self) -> None:
        for value in [{"paragraphs": [{"section": "S", "text": "Fact", "evidence_ids": []}]},
                      {"paragraphs": [{"section": "S", "text": "Fact", "evidence_ids": ["invented"]}]}]:
            with self.assertRaises(ValueError):
                pipeline.validate_paragraphs(value, {"E1"})
        value = {"paragraphs": [{"section": "S", "text": "Fact", "evidence_ids": ["E1"]}]}
        with self.assertRaisesRegex(ValueError, "dropped"):
            pipeline.validate_paragraphs(value, {"E1", "E2"}, {"E1", "E2"})

    def test_truncated_writer_cannot_publish_or_cache(self) -> None:
        self.work.mkdir(parents=True)
        client = pipeline.LocalHTTP("http://127.0.0.1:8000/v1")
        with patch.object(client, "post", return_value={"choices": [{"finish_reason": "length"}]}):
            with self.assertRaisesRegex(ValueError, "truncated"):
                pipeline.writer_call(client, self.work, {}, [], set(), self.args)
        self.assertFalse(list((self.work / "writing").rglob("*.json")))

    def test_bounded_passages_preserve_long_unbroken_text(self) -> None:
        body = "A" * 17000
        chunks = corpus.split_passages({"doc_id": "a", "name": "A", "body": body})
        self.assertEqual("".join(c["text"] for c in chunks), body)
        self.assertLessEqual(max(len(c["text"]) for c in chunks), 6000)

    def test_path_source_confinement_and_unknown_source(self) -> None:
        with self.assertRaises(ValueError):
            pipeline.confined(self.root, "../outside")
        path = self.fixture("a", "a")
        text = path.read_text(encoding="utf-8").replace('source_id: "src-' + "a" * 16 + '"', 'source_id: "../../outside"')
        path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            corpus.read_document(path, self.out / "documents")
        for endpoint in ["https://example.com/v1", "http://192.168.1.5/v1"]:
            with self.assertRaisesRegex(ValueError, "local"):
                pipeline.LocalHTTP(endpoint)

    def test_safe_recovery_of_partially_published_output_and_process_lock(self) -> None:
        folder = self.out / "test"
        pipeline.atomic_text(folder / "document.md", "old")
        old_hash = pipeline.file_hash(folder / "document.md")
        pipeline.protect_outputs(folder, {"document.md": "new-hash"}, {"document.md": old_hash})
        with self.assertRaises(ValueError):
            pipeline.protect_outputs(folder, {"document.md": "new-hash"})
        with pipeline.stage_lock(self.work):
            with self.assertRaisesRegex(RuntimeError, "Another"):
                with pipeline.stage_lock(self.work):
                    pass
        with pipeline.stage_lock(self.work):
            pass

    def test_cli_status_and_help_do_not_build_or_contact_model(self) -> None:
        script = ROOT / "content" / "transcriptions" / "phase4_merge.py"
        for command in [["status", "--root", str(self.root)], ["--help"]]:
            result = subprocess.run([sys.executable, str(script), *command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.work.exists())


if __name__ == "__main__":
    unittest.main()
