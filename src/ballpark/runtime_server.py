"""Small portable HTTP surface for a compiled UI and accepted runtime publications."""

from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ballpark.runtime_app import runtime_readiness


class RuntimeHandler(SimpleHTTPRequestHandler):
    web_dir: Path
    publication_dir: Path
    state_dir: Path

    def _json(self, code: int, value: object) -> None:
        body = json.dumps(value, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def list_directory(self, _path: str) -> None:
        self.send_error(404, "directory listing disabled")
        return None

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(200, {"state": "live"})
            return
        if path == "/readiness":
            result = runtime_readiness(self.state_dir, heartbeat_seconds=180, job_lag_seconds=30)
            self._json(200 if result["state"] == "ready" else 503, result)
            return
        allowed = {"/data/data.json", "/data/release.json", "/archive/index.json"}
        archive_name = path.removeprefix("/archive/")
        if path in allowed or (
            path.startswith("/archive/")
            and archive_name.endswith(".json")
            and len(archive_name) == 15
            and archive_name[:10].count("-") == 2
        ):
            try:
                pointer = json.loads(
                    (self.publication_dir / "current.json").read_text(encoding="utf-8")
                )
                token = pointer["token"]
                target = self.publication_dir / "releases" / token / path.lstrip("/")
                if not target.is_file():
                    raise FileNotFoundError
                body = target.read_bytes()
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                self._json(404, {"state": "not_published"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/data/") or path.startswith("/archive/"):
            self._json(404, {"state": "not_found"})
            return
        self.directory = str(self.web_dir)
        super().do_GET()


def serve(*, web_dir: Path, publication_dir: Path, state_dir: Path, host: str, port: int) -> None:
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("host must be 127.0.0.1 or 0.0.0.0")
    handler = type(
        "ConfiguredRuntimeHandler",
        (RuntimeHandler,),
        {"web_dir": web_dir, "publication_dir": publication_dir, "state_dir": state_dir},
    )
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
