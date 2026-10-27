"""Loopback-only pilot review. Serve explicit artifacts, never runtime/config files."""
import html
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from .anthropic_extraction import _html_page
from .store import Store


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send(self, status, body, content_type='text/html; charset=utf-8'):
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type); self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host', '').casefold() not in allowed:
            return self.send(403, 'Local access only')
        origin = self.headers.get('Origin')
        if origin and origin.casefold() not in {'http://' + host for host in allowed}:
            return self.send(403, 'Local access only')
        path = urlsplit(self.path).path
        if path in {'/', '/index.html'}:
            return self.send(200, (self.server.root / 'index.html').read_bytes())
        if path == '/status':
            return self.send(200, (self.server.root / 'report.json').read_bytes(), 'application/json; charset=utf-8')
        match = re.fullmatch(r'/articles/(article-[a-f0-9]{32})\.(html|json)', path)
        if match:
            file = self.server.root / 'articles' / (match[1] + '.' + match[2])
            if file.is_file():
                return self.send(200, file.read_bytes(), 'application/json; charset=utf-8' if match[2] == 'json' else 'text/html; charset=utf-8')
        match = re.fullmatch(r'/sources/(article-[a-f0-9]{32})/(rec-[a-f0-9-]{36})', path)
        if match:
            with closing(sqlite3.connect((self.server.root / 'articles.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
                row = db.execute('SELECT bundle_json FROM jobs WHERE id=?', (match[1],)).fetchone()
            docs = json.loads(row[0])['source_documents'] if row else []
            doc = next((d for d in docs if d['id'] == match[2]), None)
            if doc:
                main = Store(self.server.database, readonly=True)
                try:
                    original = main.db.execute('SELECT body FROM record_versions WHERE id=? AND record_id=?',
                                               (doc['version_id'], doc['id'])).fetchone()
                finally: main.close()
                if original:
                    body = '<p><a href="/articles/' + match[1] + '.html#sources">← Reconstruction et sources</a></p>'
                    body += '<h1>' + html.escape(doc.get('name', doc['id'])) + '</h1><p>' + html.escape(doc.get('source_title', '')) + '</p>'
                    body += '<p>Version source originale utilisée par ce pilote. Texte intégral.</p><pre>' + html.escape(original[0]) + '</pre>'
                    return self.send(200, _html_page(doc.get('name', ''), body))
        self.send(404, 'Not found')

    def do_POST(self): self.send(405, 'Read only')
    do_PUT = do_DELETE = do_PATCH = do_POST


def make_server(root, database, port=8769):
    root = Path(root).resolve()
    if not (root / 'index.html').is_file() or not Path(database).is_file():
        raise ValueError('Prepared pilot and source database are required')
    server = ThreadingHTTPServer(('127.0.0.1', port), ReviewHandler)
    server.daemon_threads = True; server.root = root; server.database = Path(database).resolve()
    return server
