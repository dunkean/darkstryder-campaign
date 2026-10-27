"""One bounded JSON relay request, with identity checks on our WSL server process."""
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request


def main():
    request = json.load(sys.stdin)
    port = int(request["port"])
    method, path = request["method"], request["path"]
    if not 1 <= port <= 65535 or (method, path) not in {("GET", "/v1/models"), ("POST", "/v1/classifier")}:
        raise ValueError("Invalid loopback route")
    expected = request["runtime"]
    proc = Path("/proc") / str(int(expected["server_pid_wsl"]))

    def identity():
        argv = [arg.decode() for arg in (proc / "cmdline").read_bytes().split(b"\0") if arg]
        ticks = (proc / "stat").read_text().split(") ", 1)[1].split()[19]
        if argv != expected["launch_argv"] or ticks != str(expected["server_start_ticks"]):
            raise ValueError("Server process does not match launch receipt")
        return ticks

    before = identity()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    payload = request["body"].encode() if method == "POST" else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=payload, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        response = opener.open(req, timeout=int(request["timeout"]))
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read(8 * 1024 * 1024 + 1)
        status = response.status
    if len(body) > 8 * 1024 * 1024 or identity() != before:
        raise ValueError("Response size or server identity changed")
    json.loads(body)
    json.dump({"status": status, "body": body.decode(), "server_pid_wsl": int(expected["server_pid_wsl"]),
               "server_start_ticks": before}, sys.stdout)


if __name__ == "__main__":
    main()
