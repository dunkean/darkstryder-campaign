"""Bounded read-only localhost API for agents; stdlib, no model or file routes."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .store import Store
from . import review
from . import review_workspace


MAX_LIMIT = 100
MAX_PASSAGES = 50
PATH_KEYS = {"path", "file", "filename", "database", "download_root", "runtime_root", "manifest_locator"}


def safe_value(value):
    """Keep documentary locators (IDs/pointers/units), omit local filesystem metadata."""
    if isinstance(value, dict):
        return {key: safe_value(item) for key, item in value.items()
                if key.casefold() not in PATH_KEYS and not key.casefold().endswith("_path")}
    if isinstance(value, list):
        return [safe_value(item) for item in value]
    return value


def bounded_value(value, maximum=12000):
    value = safe_value(value)
    length = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return value if length <= maximum else {"omitted": True, "characters": length}


def record_summary(record, excerpt=300):
    rid = record["id"]
    result = {key: record[key] for key in ("id", "entity_id", "source_id", "source_key", "current_version", "active",
                                                   "name", "kind", "universe", "origin", "sha256")}
    result.update(aliases=bounded_value(record["aliases"], 3000), scope=bounded_value(record["scope"], 4000),
                  body_excerpt=record["body"][:excerpt], body_characters=len(record["body"]),
                  excerpt_only=len(record["body"]) > excerpt,
                  record_url="/records/" + quote(rid, safe=""),
                  entity_url="/entities/" + quote(record["entity_id"], safe=""),
                  passages_url="/passages?" + urlencode({"record_id": rid, "offset": 0, "limit": 5}))
    return result


def decoded_row(row, budget=4000):
    result = {}
    for key, value in dict(row).items():
        if key.endswith("_json") and value is not None:
            result[key[:-5]] = bounded_value(json.loads(value), budget)
        elif key not in PATH_KEYS:
            result[key] = value
    return safe_value(result)


class ReadOnlyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "SWKnowledge/1"
    sys_version = ""

    def log_message(self, *args):
        pass

    def respond(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def static(self, name, content_type):
        raw = Path(__file__).with_name(name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {"error": "unsupported_request" if code == 501 else "http_error", "status": code})

    def do_POST(self):
        size = self.headers.get("Content-Length", "0")
        if size.isdigit() and int(size) <= 8192:
            self.rfile.read(int(size))
        self.respond(405, {"error": "read_only", "message": "Only GET is supported"})

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST
    do_HEAD = do_POST

    def local_request(self):
        expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        if self.server.server_port == 80:
            expected.update({"127.0.0.1", "localhost"})
        host = self.headers.get("Host", "").casefold()
        if host not in expected:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin.casefold() in {"http://" + item for item in expected}

    @staticmethod
    def number(params, name, default, maximum):
        raw = params.get(name, str(default))
        if not re.fullmatch(r"\d{1,10}", raw):
            raise ValueError(f"Invalid {name}")
        value = int(raw)
        if value > maximum or (name.endswith("limit") and value < 1):
            raise ValueError(f"{name} outside allowed range")
        return value

    @staticmethod
    def identifier(value):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise ValueError("Invalid identifier")
        return value

    @staticmethod
    def pagination(path, params, offset, limit, total):
        return {"offset": offset, "limit": limit, "total": total,
                "next_url": path + "?" + urlencode({**params, "offset": offset + limit, "limit": limit})
                if offset + limit < total else None}

    def do_GET(self):
        if not self.local_request():
            self.respond(403, {"error": "local_origin_required"})
            return
        store = None
        try:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or len(self.path) > 8192:
                raise ValueError("Invalid request target")
            multi = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
            if any(len(values) != 1 for values in multi.values()):
                raise ValueError("Duplicate query parameter")
            params = {key: values[0] for key, values in multi.items()}
            static = {'/review': ('review_ui.html', 'text/html; charset=utf-8'),
                      '/review/ui.css': ('review_ui.css', 'text/css; charset=utf-8'),
                      '/review/ui.js': ('review_ui.js', 'text/javascript; charset=utf-8')}
            if parsed.path in static:
                self.static(*static[parsed.path])
                return
            store = Store(self.server.database, readonly=True)
            # Each response sees one consistent SQLite snapshot even during imports.
            store.db.execute("BEGIN")
            result = self.route(store, parsed.path, params)
            self.respond(200, result)
        except KeyError:
            self.respond(404, {"error": "not_found"})
        except ValueError as error:
            self.respond(400, {"error": "invalid_request", "message": str(error)[:160]})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (sqlite3.Error, OSError):
            self.respond(503, {"error": "database_unavailable"})
        except Exception:
            self.respond(500, {"error": "internal_error"})
        finally:
            if store is not None:
                store.close()

    def route(self, store, path, params):
        workspace_prefix = '/review/api/workspace/'
        document_prefix = workspace_prefix + 'documents/'
        if path.startswith(document_prefix):
            parts = path[len(document_prefix):].split('/')
            if len(parts) == 2 and parts[1] == 'paragraphs':
                return review_workspace.paragraphs(store, self.identifier(parts[0]), params, self.server.selection_database)
            if len(parts) == 3 and parts[1] == 'requests':
                return review_workspace.evaluated_request(store, self.identifier(parts[0]), self.identifier(parts[2]), self.server.selection_database)
            raise KeyError(path)
        if path.startswith(workspace_prefix):
            return review_workspace.workspace(store, self.identifier(path[len(workspace_prefix):]), params, self.server.selection_database)
        if path == '/aggregates':
            return review.aggregates(store, params)
        if path == '/aggregate-links/evidence':
            return review.aggregate_evidence(store, params)
        if path.startswith('/aggregates/') and path.endswith('/text'):
            return review.aggregate_text(store,self.identifier(path[len('/aggregates/'):-len('/text')]),params)
        if path.startswith('/aggregates/'):
            return review.aggregate(store, self.identifier(path[len('/aggregates/'):]), params)
        if path == '/review/api/filters':
            return review.filters(store)
        if path == '/review/api/coverage':
            return review.coverage(store)
        if path == '/review/api/curation/jobs':
            return review.native_curation_jobs(store,params)
        if path == '/review/api/curation/previews':
            return review.native_curation_previews(store,params)
        if path == '/review/api/questions':
            return review.questions(store, params)
        if path == '/review/api/documents':
            return review.documents(store, params)
        if path == '/review/api/browse/types':
            return review.aggregate_types(store, params)
        if path == '/review/api/browse/aggregates':
            return review.aggregates(store, params)
        if path == '/review/api/browse/evidence':
            return review.aggregate_evidence(store, params)
        if path.startswith('/review/api/browse/aggregates/') and path.endswith('/text'):
            return review.aggregate_text(store,self.identifier(path[len('/review/api/browse/aggregates/'):-len('/text')]),params)
        if path.startswith('/review/api/browse/aggregates/'):
            return review.aggregate(store, self.identifier(path[len('/review/api/browse/aggregates/'):]), params)
        if path == '/review/api/entities':
            return review.entities(store, params)
        if path.startswith('/review/api/questions/'):
            return review.question(store, self.identifier(path[len('/review/api/questions/'):]))
        if path.startswith('/review/api/documents/'):
            return review.document(store, self.identifier(path[len('/review/api/documents/'):]))
        if path.startswith('/review/api/entities/'):
            return review.entity(store, self.identifier(path[len('/review/api/entities/'):]), params)
        if path == "/status":
            return store.status()
        if path == "/search":
            query = params.get("q", "").strip()
            if not query or len(query) > 500:
                raise ValueError("q must contain 1 to 500 characters")
            limit = self.number(params, "limit", 20, MAX_LIMIT)
            rows = store.search(query, limit, params.get("kind"), params.get("universe", "all"), params.get("origin"), params.get("importance"))
            if review._aggregates_available(store):
                for row in rows:
                    aggregate = store.db.execute('SELECT a.id,a.key,a.kind,a.universe FROM aggregate_members m JOIN aggregates a ON a.id=m.aggregate_id WHERE m.record_id=?',(row['record_id'],)).fetchone()
                    if aggregate:
                        row.update(aggregate_id=aggregate['id'],aggregate_key=aggregate['key'],aggregate_url='/aggregates/'+aggregate['id'],aggregate_kind=aggregate['kind'],aggregate_universe=aggregate['universe'])
            return {"query": query, "limit": limit, "results": [
                {**row, "record_url": "/records/" + quote(row["record_id"], safe=""),
                 "entity_url": "/entities/" + quote(row["entity_id"], safe="")} for row in rows]}
        if path.startswith("/records/"):
            rid = self.identifier(path[len("/records/"):])
            record = store.record(rid)
            result = record_summary(record, 1200)
            if review._aggregates_available(store):
                aggregate = store.db.execute('SELECT a.id,a.key,a.kind,a.universe FROM aggregate_members m JOIN aggregates a ON a.id=m.aggregate_id WHERE m.record_id=?',(rid,)).fetchone()
                if aggregate:
                    result.update(aggregate_id=aggregate['id'],aggregate_key=aggregate['key'],aggregate_url='/aggregates/'+aggregate['id'],aggregate_kind=aggregate['kind'],aggregate_universe=aggregate['universe'])
            result.update(locator=bounded_value(record["locator"], 4000), payload=bounded_value(record["payload"]))
            result['annotations'] = [decoded_row(row) for row in store.db.execute('SELECT * FROM annotations WHERE record_id=? ORDER BY task', (rid,))]
            profile_offset = self.number(params, "profile_offset", 0, 2_147_483_647)
            profile_limit = self.number(params, "profile_limit", 30, MAX_LIMIT)
            version = record["current_version"]
            result["profiles"] = [{"id": row["id"], "record_id": rid, "version_id": row["version_id"],
                                   "scope": bounded_value(json.loads(row["scope_json"]), 4000)}
                                  for row in store.db.execute("SELECT id,version_id,scope_json FROM profiles WHERE record_id=? AND version_id=? ORDER BY id", (rid, version))]
            base = " FROM profile_values pv JOIN profiles p ON p.id=pv.profile_id WHERE p.record_id=? AND p.version_id=?"
            total = store.db.execute("SELECT count(*)" + base, (rid, version)).fetchone()[0]
            result["profile_values"] = [{"profile_id": row["profile_id"], "field": row["field"],
                                          "value": bounded_value(json.loads(row["value_json"]), 4000),
                                          "source_pointer": row["source_path"], "unit": row["unit"]}
                                         for row in store.db.execute("SELECT pv.*" + base +
                                             " ORDER BY pv.profile_id,pv.field,pv.source_path LIMIT ? OFFSET ?",
                                             (rid, version, profile_limit, profile_offset))]
            result["profile_pagination"] = {"offset": profile_offset, "limit": profile_limit, "total": total,
                "next_url": path + "?" + urlencode({**params, "profile_offset": profile_offset + profile_limit,
                                                       "profile_limit": profile_limit}) if profile_offset + profile_limit < total else None}
            return result
        if path.startswith("/entities/"):
            eid = self.identifier(path[len("/entities/"):])
            entity = store.entity(eid)
            offset = self.number(params, "offset", 0, 2_147_483_647)
            limit = self.number(params, "limit", 20, MAX_LIMIT)
            rel_offset = self.number(params, "relation_offset", 0, 2_147_483_647)
            rel_limit = self.number(params, "relation_limit", 20, MAX_LIMIT)
            doc_offset = self.number(params, "document_link_offset", 0, 2_147_483_647)
            doc_limit = self.number(params, "document_link_limit", 20, MAX_LIMIT)
            records = entity["records"][offset:offset + limit]
            ids = {record["id"] for record in records}
            profiles = [{key: value for key, value in decoded_row(row).items() if key != "stats"}
                        for row in entity["profiles"] if row["record_id"] in ids]
            for profile in profiles:
                profile["record_url"] = "/records/" + quote(profile["record_id"], safe="")
            relations = [decoded_row(row) for row in entity["relations"][rel_offset:rel_offset + rel_limit]]
            relation_next = path + "?" + urlencode({**params, "relation_offset": rel_offset + rel_limit,
                                                    "relation_limit": rel_limit}) if rel_offset + rel_limit < len(entity["relations"]) else None
            document_links = entity.get("document_links", [])
            doc_next = path + "?" + urlencode({**params, "document_link_offset": doc_offset + doc_limit,
                                               "document_link_limit": doc_limit}) if doc_offset + doc_limit < len(document_links) else None
            doc_rows = [decoded_row(row) for row in document_links[doc_offset:doc_offset + doc_limit]]
            for row in [*relations, *doc_rows]:
                for role in ("subject", "object"):
                    if role + "_record_id" in row:
                        row[role + "_record_url"] = "/records/" + quote(row[role + "_record_id"], safe="")
            return {"entity": entity["entity"], "resolution_status": entity['resolution_status'], "records": [record_summary(record) for record in records],
                    "record_pagination": self.pagination(path, params, offset, limit, len(entity["records"])),
                    "profiles": profiles, "relations": relations,
                    "relation_pagination": {"offset": rel_offset, "limit": rel_limit, "total": len(entity["relations"]), "next_url": relation_next},
                    "document_links": doc_rows,
                    "document_links_total": len(document_links),
                    "document_links_truncated": len(doc_rows) < len(document_links),
                    "document_link_pagination": {"offset": doc_offset, "limit": doc_limit, "total": len(document_links), "next_url": doc_next},
                    "annotations": [decoded_row(row) for row in entity["annotations"] if row["record_id"] in ids],
                    "editorial": decoded_row(entity["editorial"]) if entity["editorial"] else None,
                    "editorial_overlays": [decoded_row(row) for row in entity.get("editorial_overlays", [])[:MAX_LIMIT]]}
        if path == "/passages":
            rid = self.identifier(params.get("record_id", ""))
            record = store.record(rid)
            offset = self.number(params, "offset", 0, 2_147_483_647)
            limit = self.number(params, "limit", 5, MAX_PASSAGES)
            version = record["current_version"]
            total = store.db.execute("SELECT count(*) FROM passages WHERE version_id=?", (version,)).fetchone()[0]
            rows = [dict(row) for row in store.db.execute(
                "SELECT id,sequence,text,offset_start,offset_end FROM passages WHERE version_id=? ORDER BY sequence LIMIT ? OFFSET ?",
                (version, limit, offset))]
            return {"record_id": rid, "version_id": version, "passages": rows,
                    "pagination": self.pagination(path, {"record_id": rid}, offset, limit, total)}
        raise KeyError(path)


def make_server(database, port=0, selection_database=None):
    """Create an unstarted loopback server. Port 0 selects a temporary test port."""
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError("Knowledge database must exist before serving")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Invalid local server port")
    server = ReadOnlyServer(("127.0.0.1", port), Handler)
    server.database = path
    server.selection_database = Path(selection_database).resolve() if selection_database else review_workspace.discover_selection(path)
    return server


def serve(database, port=8765, selection_database=None):
    """Serve until interrupted; always binds 127.0.0.1 and opens SQLite read-only."""
    server = make_server(database, port, selection_database)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
