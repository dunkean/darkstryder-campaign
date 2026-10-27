"""Windows loopback relay when WSL's automatic localhost forwarding is unavailable.

No global network/WSL setting is changed. Only the classifier's two JSON routes
are exposed; each request uses a bounded stdlib worker against WSL loopback.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-22.04")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--upstream-port", type=int, default=8000)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--runtime-report", type=Path, required=True)
    parser.add_argument("--runtime-receipt", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.upstream_port <= 65535 or args.timeout < 1:
        parser.error("Invalid port or timeout")

    class Handler(BaseHTTPRequestHandler):
        def runtime(self):
            raw_report = args.runtime_report.read_bytes()
            raw_receipt = args.runtime_receipt.read_bytes()
            report, receipt = json.loads(raw_report), json.loads(raw_receipt)
            if not isinstance(report.get("device_map"), dict) or not report.get("logits_index_adapter"):
                raise RuntimeError("Model load has not completed")
            argv = receipt["launch_argv"]
            if argv[argv.index("--model") + 1] != report["model_path"] or argv[argv.index("--revision") + 1] != report["revision"]:
                raise RuntimeError("Report does not match launch receipt")
            value = {"effective_revision": report["effective_revision"], "revision_source": "local_launcher_report",
                     "server_pid_wsl": int(receipt["pid"]), "server_start_ticks": str(receipt["server_start_ticks"])}
            return value, argv, report["served_model"], hashlib.sha256(raw_report + raw_receipt).hexdigest()

        def reply(self, status, body):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def forward(self):
            host = urlsplit("http://" + self.headers.get("Host", "")).hostname
            origin = self.headers.get("Origin")
            if host not in {"localhost", "127.0.0.1"} or (origin and urlsplit(origin).hostname not in {"localhost", "127.0.0.1"}):
                return self.reply(403, {"error": "Loopback requests only"})
            allowed = {("GET", "/v1/models"), ("POST", "/v1/classifier")}
            if (self.command, self.path) not in allowed:
                return self.reply(404, {"error": "Route unavailable"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 8 * 1024 * 1024:
                    return self.reply(413, {"error": "Request exceeds limit"})
                body = self.rfile.read(size)
                if self.command == "POST" and not isinstance(json.loads(body), dict):
                    raise ValueError("Expected a JSON object")
                runtime, argv, model, signature = self.runtime()
                worker = Path(__file__).resolve().with_name("openjev-wsl-forward.py").as_posix()
                worker = "/mnt/" + worker[0].lower() + worker[2:]
                command = ["wsl.exe", "-d", args.distribution, "--exec", "/usr/bin/python3", worker]
                request = {"port": args.upstream_port, "path": self.path, "method": self.command,
                           "body": body.decode(), "timeout": args.timeout, "runtime": {**runtime, "launch_argv": argv}}
                result = subprocess.run(command, input=json.dumps(request).encode(), capture_output=True, timeout=args.timeout + 10,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if result.returncode:
                    self.log_error("WSL forward failed: %s", result.stderr.decode(errors="replace")[:1000])
                    return self.reply(502, {"error": "Local WSL classifier request failed", "exit_code": result.returncode})
                forwarded = json.loads(result.stdout)
                payload = json.loads(forwarded["body"])
                if signature != self.runtime()[3]:
                    return self.reply(503, {"error": "Local model revision changed during request"})
                if forwarded["status"] == 200:
                    ids = [entry.get("id") for entry in payload.get("data", [])] if self.command == "GET" else [payload.get("model")]
                    if model not in ids:
                        return self.reply(502, {"error": "Local model ID mismatch"})
                    payload["local_runtime"] = runtime
                self.reply(int(forwarded["status"]), payload)
            except (ValueError, json.JSONDecodeError):
                self.reply(400, {"error": "Invalid JSON request or upstream response"})
            except (RuntimeError, KeyError, TypeError):
                self.reply(503, {"error": "Local runtime receipt is not ready or consistent"})
            except subprocess.TimeoutExpired:
                self.reply(504, {"error": "Local classifier timed out"})
            except OSError:
                self.reply(502, {"error": "Local WSL relay unavailable"})

        do_GET = forward
        do_POST = forward

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"LOOPBACK_RELAY_READY http://127.0.0.1:{args.port}/v1", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
