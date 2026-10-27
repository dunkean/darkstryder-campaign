"""Real loopback HTTP tests against temporary knowledge stores; no inference."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.sw_kbase.service import make_server
from packages.sw_kbase.store import Store


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.database = Path(cls.tmp.name) / "knowledge.sqlite"
        store = Store(cls.database)
        sid = store.source("fixture", "Fixture")
        cls.body = "Équipage Alpha " + "x" * 7200
        cls.first, _ = store.ingest(sid, "one", "Alpha", body=cls.body,
                                   payload={"stats": {"dex": "3D"}, "local_path": "D:/private/source.pdf"},
                                   locator={"kind": "json_pointer", "file": "secret/input.json", "pointer": "/items/0"},
                                   profile={"dex": "3D", "hull": "4D+2", "cost": 150}, scope={"edition": "2E"})
        cls.second, _ = store.ingest(sid, "two", "Alpha alternate", body="Second reference", profile={"dex": "4D"}, scope={"edition": "REUP"})
        proposal = store.enqueue("identity", cls.first, cls.second)
        store.store_result(proposal, {"accepted": True, "decision": "same_entity"})
        store.resolve_identity(proposal)
        cls.entity_id = store.record(cls.first)["entity_id"]
        store.db.execute("INSERT INTO document_links VALUES(?,?,?,?,?,?)", (
            cls.first, cls.second, "cited_unit", store.record(cls.first)["current_version"],
            store.record(cls.second)["current_version"], json.dumps({"declared": True, "file": "private/file.md"})))
        store.db.commit()
        cls.before = store.status()
        store.close()
        cls.server = make_server(cls.database, port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.tmp.cleanup()

    def request(self, path, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw) if raw else None
        finally:
            connection.close()

    def test_status_search_and_no_cors(self):
        status, headers, value = self.request("/status")
        self.assertEqual(status, 200)
        self.assertEqual(value["records"], 2)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertIn("application/json", headers["Content-Type"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, _, value = self.request("/search?" + urlencode({"q": "Équipage", "limit": 1, "universe": "all"}))
        self.assertEqual(status, 200)
        self.assertEqual(value["results"][0]["record_id"], self.first)
        self.assertTrue(value["results"][0]["entity_url"].startswith("/entities/"))

    def test_record_is_bounded_and_hides_filesystem_locators(self):
        status, _, value = self.request("/records/" + self.first)
        self.assertEqual(status, 200)
        self.assertEqual(len(value["body_excerpt"]), 1200)
        self.assertTrue(value["excerpt_only"])
        self.assertEqual(value["payload"]["stats"]["dex"], "3D")
        self.assertEqual(value["locator"]["pointer"], "/items/0")
        serialized = json.dumps(value)
        self.assertNotIn("D:/private", serialized)
        self.assertNotIn("secret/input", serialized)
        self.assertNotIn("local_path", serialized)

    def test_complete_text_is_available_through_current_passages(self):
        path = "/passages?" + urlencode({"record_id": self.first, "limit": 1})
        text, seen = [], []
        while path:
            status, _, value = self.request(path)
            self.assertEqual(status, 200)
            self.assertLessEqual(len(value["passages"]), 1)
            text.extend(row["text"] for row in value["passages"])
            seen.extend(row["id"] for row in value["passages"])
            path = value["pagination"]["next_url"]
        self.assertEqual("".join(text), self.body)
        self.assertEqual(len(seen), len(set(seen)))

    def test_entity_records_are_paginated_and_document_links_are_present(self):
        status, _, value = self.request("/entities/" + self.entity_id + "?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(value["records"]), 1)
        self.assertEqual(value["record_pagination"]["total"], 2)
        self.assertNotIn("stats", value["profiles"][0])
        self.assertEqual(value["document_links_total"], 1)
        self.assertNotIn("file", value["document_links"][0]["context"])
        status, _, page2 = self.request(value["record_pagination"]["next_url"])
        self.assertEqual(status, 200)
        self.assertNotEqual(page2["records"][0]["id"], value["records"][0]["id"])
        self.assertIsNone(page2["record_pagination"]["next_url"])

    def test_normalized_profile_values_are_paginated_and_keep_variants_separate(self):
        path = "/records/" + self.first + "?profile_limit=1"
        values = []
        profile_id = None
        while path:
            status, _, value = self.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(value["profiles"][0]["scope"]["edition"], "2E")
            profile_id = profile_id or value["profiles"][0]["id"]
            self.assertEqual(value["profiles"][0]["id"], profile_id)
            self.assertLessEqual(len(value["profile_values"]), 1)
            values.extend(value["profile_values"])
            path = value["profile_pagination"]["next_url"]
        self.assertEqual(len(values), 3)
        self.assertEqual({row["profile_id"] for row in values}, {profile_id})
        hull = next(row for row in values if row["field"] == "hull")
        self.assertEqual(hull["value"]["dice"], 4)
        self.assertEqual(hull["value"]["pips"], 2)
        self.assertEqual(hull["source_pointer"], "/hull")
        status, _, other = self.request("/records/" + self.second)
        self.assertEqual(status, 200)
        self.assertNotEqual(other["profiles"][0]["id"], profile_id)
        self.assertEqual(other["profiles"][0]["scope"]["edition"], "REUP")
        self.assertEqual(other["profile_values"][0]["value"]["dice"], 4)

    def test_mutations_and_cross_origin_requests_are_refused(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            status, headers, value = self.request("/records/" + self.first, method, body="{}")
            self.assertEqual(status, 405)
            self.assertEqual(value["error"], "read_only")
            self.assertNotIn("Access-Control-Allow-Origin", headers)
        for headers in ({"Host": "malicious.invalid"}, {"Origin": "https://remote.invalid"}):
            self.assertEqual(self.request("/status", headers=headers)[0], 403)
        store = Store(self.database, readonly=True)
        try:
            self.assertEqual(store.status(), self.before)
        finally:
            store.close()

    def test_invalid_requests_return_bounded_json_errors(self):
        cases = [("/missing", 404), ("/records/rec-missing", 404), ("/records/../../secret", 400),
                 ("/search", 400), ("/search?q=Alpha&limit=101", 400),
                 ("/search?q=Alpha&q=Other", 400), ("/passages?record_id=" + self.first + "&offset=-1", 400),
                 ("/passages?record_id=" + self.first + "&limit=51", 400),
                 ("/records/" + self.first + "?profile_limit=101", 400),
                 ("/entities/" + self.entity_id + "?limit=0", 400)]
        for path, expected in cases:
            status, headers, value = self.request(path)
            self.assertEqual(status, expected, path)
            self.assertIn("error", value)
            self.assertNotIn(str(self.database), json.dumps(value))
            self.assertIn("application/json", headers["Content-Type"])

    def test_concurrent_requests_have_separate_readonly_connections(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.request("/status"), range(18)))
        self.assertTrue(all(status == 200 and value["records"] == 2 for status, _, value in results))


if __name__ == "__main__":
    unittest.main()
