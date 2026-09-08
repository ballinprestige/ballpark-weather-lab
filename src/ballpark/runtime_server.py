"""Small portable HTTP surface for a compiled UI and accepted runtime publications."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ballpark.runtime_app import (
    _DEFAULT_MAX_OBJECT_BYTES,
    _environment_limit,
    _is_digest,
    _is_token,
    _load_json,
    _load_object_json,
    _read_object,
    _safe_child,
    _validated_index,
    runtime_readiness,
)


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
                maximum = _environment_limit("BALLPARK_MAX_OBJECT_BYTES", _DEFAULT_MAX_OBJECT_BYTES)
                pointer = _load_json(
                    self.publication_dir / "pointer.json", maximum, "accepted pointer is malformed"
                )
                if not isinstance(pointer, dict) or not isinstance(pointer.get("current"), dict):
                    raise RuntimeError("accepted pointer is malformed")
                token = pointer["current"].get("token")
                if not _is_token(token):
                    raise RuntimeError("invalid release token")
                manifest = _load_json(
                    _safe_child(self.publication_dir / "releases", token) / "manifest.json",
                    maximum,
                    "accepted manifest is malformed",
                )
                if not isinstance(manifest, dict) or manifest.get("token") != token:
                    raise RuntimeError("accepted manifest is malformed")
                field = {
                    "/data/data.json": "data_sha256",
                    "/data/release.json": "release_sha256",
                    "/archive/index.json": "archive_index_sha256",
                }.get(path)
                objects = self.publication_dir / "objects"
                index = _load_object_json(
                    objects,
                    manifest.get("archive_index_sha256"),
                    maximum,
                    "accepted archive index is malformed",
                )
                rows = _validated_index(index, require_object=True)
                if archive_date is not None:
                    row = next(row for row in rows if row["date"] == archive_date)
                    field_value = row["object_sha256"]
                else:
                    field_value = manifest[field]  # type: ignore[index]
                if not _is_digest(field_value):
                    raise RuntimeError("accepted object digest is malformed")
                body = _read_object(objects, field_value, maximum)
                if archive_date is not None and (
                    hashlib.sha256(body).hexdigest() != row["payload_sha256"]
                ):
                    raise RuntimeError("accepted archive object digest is malformed")
            except (
                EOFError,
                OSError,
                KeyError,
                StopIteration,
                TypeError,
                ValueError,
                RuntimeError,
                json.JSONDecodeError,
            ):
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
