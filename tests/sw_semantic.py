"""Local semantic contracts: fake inference only, no models or corpus access."""
import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.semantic import ContextBudgetError, LocalHTTP, SemanticClient, TASKS, WriterClient


class SemanticTests(unittest.TestCase):
    def client(self, **config):
        return SemanticClient({"semantic_model": "test", "semantic_revision": "r1", **config})

    def response(self, payload, label="same_entity", score=.98):
        answers = {}
        for key, question in payload["questions"].items():
            labels = question["criteria"]
            answers[key] = {"choice": label, "probabilities": {
                k: score if k == label else (1 - score) / (len(labels) - 1) for k in labels}}
        return {"model": "test", "answers": answers}

    def test_shared_context_and_independent_results(self):
        client, sent = self.client(), []
        def post(route, payload):
            sent.append(payload)
            return self.response(payload)
        client.http.post = post
        jobs = [{"id": str(i), "task": "identity", "context": {
            "TARGET": {"id": "a", "name": "FarStar"}, "CANDIDATE": {"id": str(i)}}} for i in range(3)]
        result = client.evaluate(jobs)
        self.assertEqual(len(sent), 1)
        self.assertEqual(set(sent[0]["state"]['shared_context']), {"TARGET"})
        self.assertEqual(sent[0]['state']['question_contexts']['q1'], {'CANDIDATE': {'id': '1'}})
        self.assertNotIn('FarStar', sent[0]['questions']['q1']['instructions'])
        self.assertEqual([row["id"] for row in result], ["0", "1", "2"])
        self.assertTrue(all(row["accepted"] for row in result))
        self.assertEqual(len({row["fingerprint"] for row in result}), 3)

    def test_low_score_and_budget_limited_do_not_accept_negative(self):
        client = self.client(semantic_chars=1000)
        client.http.post = lambda route, payload: self.fail("Incomplete evidence must not spend an inference call")
        result = client.evaluate([{"id": "a", "task": "identity", "context": {"text": "x" * 2000}}])[0]
        self.assertIsNone(result["raw_decision"])
        self.assertFalse(result["inference_performed"])
        self.assertEqual(result["decision"], "uncertain")
        self.assertEqual(result["status"], "budget_limited")
        client.http.post = lambda route, payload: self.response(payload, "same_entity", .7)
        self.assertFalse(client.evaluate([{"id": "b", "task": "identity", "context": {}}])[0]["accepted"])

    def test_preflight_abstentions_preserve_mixed_batch_order(self):
        client, sent = self.client(semantic_chars=1000), []
        def post(route, payload):
            sent.append(payload)
            return self.response(payload)
        client.http.post = post
        jobs = [{'id': str(i), 'task': 'identity', 'context': {'text': 'x' * (2000 if i == 1 else 10)}} for i in range(3)]
        result = client.evaluate(jobs)
        self.assertEqual([row['id'] for row in result], ['0', '1', '2'])
        self.assertEqual([row['accepted'] for row in result], [True, False, True])
        self.assertEqual(len(sent), 1)
        self.assertEqual(len(sent[0]['questions']), 2)

    def test_server_token_budget_bisects_without_losing_order_or_using_partial_evidence(self):
        client, sizes = self.client(), []
        def post(route, payload):
            sizes.append(len(payload['questions']))
            if len(payload['questions']) > 1 or payload['state']['shared_context'].get('too_long'):
                raise ContextBudgetError('Rendered branch exceeds server token budget')
            return self.response(payload)
        client.http.post = post
        jobs = [{'id': str(i), 'task': 'identity', 'context': {'value': i}} for i in range(3)]
        results = client.evaluate(jobs)
        self.assertEqual([row['id'] for row in results], ['0', '1', '2'])
        self.assertTrue(all(row['accepted'] for row in results))
        result = client.evaluate([{'id': 'long', 'task': 'identity', 'context': {'too_long': True}}])[0]
        self.assertFalse(result['accepted'])
        self.assertEqual(result['reason'], 'server_token_budget')
        self.assertFalse(result['inference_performed'])

    def test_all_tasks_have_uncertain_and_revision_changes_signature(self):
        for spec in TASKS.values():
            self.assertIn("uncertain", spec["criteria"])
        job = {"id": "a", "task": "identity", "context": {"name": "Luke"}}
        self.assertNotEqual(self.client().signature(job), self.client(semantic_revision="r2").signature(job))
        self.assertNotEqual(self.client().signature(job), self.client(semantic_chars=17000).signature(job))

    def test_declared_missing_context_and_server_choice_cap(self):
        client = self.client()
        client.http.post = lambda route, payload: self.response(payload)
        for partial in [{"truncated": True}, {"missing_chunks": [2]},
                        {"original_characters": 3000, "body": "Only first paragraph"},
                        {"_context_status": {"complete": False}}]:
            result = client.evaluate([{"id": "a", "task": "identity", "context": {"TARGET": partial}}])[0]
            self.assertEqual(result["status"], "budget_limited")
            self.assertFalse(result["accepted"])
        capped = self.client(semantic_max_choice_options=5)
        with self.assertRaisesRegex(ValueError, "x_max_choice_options"):
            capped.evaluate([{"id": "t", "task": "type", "context": {}}])

    def test_invalid_probabilities_and_model_rejected(self):
        client = self.client()
        job = {"id": "a", "task": "identity", "context": {}}
        client.http.post = lambda route, payload: {**self.response(payload), "model": "wrong"}
        with self.assertRaises(ValueError):
            client.evaluate([job])
        client.http.post = lambda route, payload: {**self.response(payload), 'local_runtime': {'effective_revision': 'wrong'}}
        with self.assertRaisesRegex(ValueError, 'revision'):
            client.evaluate([job])
        client.http.post = lambda route, payload: self.response(payload, score=float("nan"))
        with self.assertRaises(ValueError):
            client.evaluate([job])

    def test_chat_is_explicit_and_has_no_probability_margin(self):
        client = self.client(semantic_backend="chat")
        client.http.post = lambda route, payload: {"model": "test", "choices": [{"finish_reason": "stop",
            "message": {"content": '{"label":"same_entity","confidence":0.99}'}}]}
        result = client.evaluate([{"id": "a", "task": "identity", "context": {}}])[0]
        self.assertIsNone(result["margin"])
        self.assertEqual(result["confidence_kind"], "self_reported")

    def writer(self, output, finish="stop"):
        client = WriterClient({"writer_model": "writer-test"})
        client.http.post = lambda route, payload: {"model": "writer-test", "choices": [
            {"finish_reason": finish, "message": {"content": json.dumps(output)}}]}
        return client

    def test_writer_records_accepted_without_book_and_unknown_refs_rejected(self):
        output = {"paragraphs": [{"section": "Profil", "text": "Variante publiée", "record_ids": ["r1"]}], "conflicts": []}
        result = self.writer(output).synthesize({"records": [{"id": "r1", "text": "Imported stats"}]})
        self.assertEqual(result["evidence_mode"], "imported_records")
        output["paragraphs"][0]["record_ids"] = ["invented"]
        with self.assertRaises(ValueError):
            self.writer(output).synthesize({"records": [{"id": "r1"}]})

    def test_writer_omitted_conflict_truncation_and_budget_rejected(self):
        output = {"paragraphs": [{"section": "Profil", "text": "A", "record_ids": ["r1"]}], "conflicts": []}
        context = {"records": [{"id": "r1"}, {"id": "r2"}], "conflicts": [{"text": "A vs B", "record_ids": ["r1", "r2"]}]}
        with self.assertRaises(ValueError):
            self.writer(output).synthesize(context)
        with self.assertRaises(ValueError):
            self.writer(output, finish="length").synthesize({"records": [{"id": "r1"}]})
        with self.assertRaises(ValueError):
            self.writer(output).synthesize({"records": [{"id": "r1", "text": "x" * 50000}]})

    def test_loopback_restriction_and_redirect_refusal(self):
        for url in ["https://127.0.0.1/v1", "http://example.org/v1", "http://127.0.0.1@evil.test/v1", "http://127.0.0.1/v1?x=1"]:
            with self.assertRaises(ValueError):
                LocalHTTP(url)
        self.assertEqual(LocalHTTP("http://localhost:8000/v1").url, "http://127.0.0.1:8000/v1")
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(302)
                self.send_header("Location", "http://example.invalid/")
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", 0), Redirect)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with self.assertRaisesRegex(RuntimeError, "302"):
                LocalHTTP(f"http://127.0.0.1:{server.server_port}/v1", attempts=1).post("/classifier", {})
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_http_token_budget_rejections_are_distinct_from_schema_errors(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                shape = json.loads(self.rfile.read(int(self.headers['Content-Length'])))['shape']
                message = "Branch for 'q0' must contain 1–16384 tokens"
                result = {'detail': message} if shape == 'detail' else {'error': {'message': message if shape == 'error' else 'Missing model field'}}
                body = json.dumps(result).encode()
                self.send_response(422)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self, *args):pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        try:
            client = LocalHTTP(f'http://127.0.0.1:{server.server_port}/v1', attempts=1)
            for shape in ('detail', 'error'):
                with self.assertRaises(ContextBudgetError):client.post('/classifier', {'shape': shape})
            with self.assertRaises(RuntimeError) as caught:client.post('/classifier', {'shape': 'schema'})
            self.assertNotIsInstance(caught.exception, ContextBudgetError)
        finally:
            server.shutdown();server.server_close();worker.join()


if __name__ == "__main__":
    unittest.main()
