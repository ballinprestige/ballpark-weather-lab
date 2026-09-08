"""Small portable HTTP surface for a compiled UI and accepted runtime publications."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date
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
        archive_date: str | None = None
        if path.startswith("/archive/") and path not in allowed:
            try:
                archive_date = date.fromisoformat(archive_name.removesuffix(".json")).isoformat()
            except ValueError:
                archive_date = None
        if path in allowed or (
            path.startswith("/archive/") and archive_name == f"{archive_date}.json"
        ):
            try:
                pointer = json.loads((self.publication_dir / "pointer.json").read_text())
                token = pointer["current"]["token"]
                if not isinstance(token, str) or len(token) != 32 or not token.isalnum():
                    raise ValueError("invalid release token")
                manifest = json.loads(
                    (self.publication_dir / "releases" / token / "manifest.json").read_text()
                )
                field = {
                    "/data/data.json": "data_sha256",
                    "/data/release.json": "release_sha256",
                    "/archive/index.json": "archive_index_sha256",
                }.get(path)
                index = json.loads(
                    gzip.decompress(
                        (
                            self.publication_dir
                            / "objects"
                            / f"{manifest['archive_index_sha256']}.json.gz"
                        ).read_bytes()
                    )
                )
                if archive_date is not None:
                    row = next(row for row in index["dates"] if row["date"] == archive_date)
                    field_value = row["object_sha256"]
                else:
                    field_value = manifest[field]  # type: ignore[index]
                target = self.publication_dir / "objects" / f"{field_value}.json.gz"
                body = gzip.decompress(target.read_bytes())
                if hashlib.sha256(body).hexdigest() != field_value:
                    raise ValueError("object digest mismatch")
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
