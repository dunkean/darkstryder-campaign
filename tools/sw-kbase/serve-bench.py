"""Loopback, read-only benchmark report. Only two explicit artifacts are served."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--port',type=int,default=8768)
a=p.parse_args();root=a.directory.resolve()
class Handler(BaseHTTPRequestHandler):
    def respond(self,head=False):
        route=urlsplit(self.path).path
        name={'/':'bench.html','/bench.html':'bench.html','/report-data.json':'report-data.json'}.get(route)
        if name is None:self.send_error(404);return
        body=(root/name).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type',('text/html' if name.endswith('.html') else 'application/json')+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        if not head:self.wfile.write(body)
    def do_GET(self):self.respond()
    def do_HEAD(self):self.respond(True)
ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
