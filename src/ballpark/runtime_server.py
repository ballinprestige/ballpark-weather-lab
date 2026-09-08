"""Small portable HTTP surface for a compiled UI and accepted runtime publications."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ballpark.runtime import parse_stamp
from ballpark.runtime_app import (
    _DEFAULT_MAX_OBJECT_BYTES,
    _environment_limit,
    _is_digest,
    _load_object_json,
    _load_snapshot,
    _read_object,
    _validate_archive_metadata,
    _validate_payload,
    _validated_index,
    _validated_release,
    runtime_readiness,
)
from ballpark.runtime_store import PublicationCatalog


class RuntimeHandler(SimpleHTTPRequestHandler):
    web_dir: Path
    publication_dir: Path
    state_dir: Path
    cache_dir: Path

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

    @staticmethod
    def _payload(body: bytes) -> dict[str, object]:
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("accepted payload is malformed") from exc
        return _validate_payload(value)

    @staticmethod
    def _current_archive_row(
        rows: list[dict[str, object]], payload: dict[str, object], digest: str, body: bytes
    ) -> dict[str, object]:
        matching = [row for row in rows if row["date"] == payload.get("date")]
        if (
            len(matching) != 1
            or matching[0].get("payload_sha256") != digest
            or matching[0].get("object_sha256") != digest
        ):
            raise RuntimeError("accepted archive history does not bind current data")
        _validate_archive_metadata(matching[0], body)
        return matching[0]

    def _source_health(self) -> dict[str, object]:
        """Publish source freshness clocks without exposing private raw evidence."""
        path = self.cache_dir / "sources" / "sportsbook.json"
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            value = envelope["value"]
            if not isinstance(value, dict) or not isinstance(value.get("date"), str):
                raise RuntimeError("source receipt is invalid")
            receipt = _load_snapshot(
                self.cache_dir,
                "sportsbook",
                date.fromisoformat(value["date"]),
            )
            latest = receipt["latest"]
            acquisition = receipt["acquisition"]
            if (
                not isinstance(receipt, dict)
                or receipt.get("schema_version") != 1
                or not isinstance(latest, dict)
                or not isinstance(acquisition, dict)
                or date.fromisoformat(receipt["date"]).isoformat() != receipt["date"]
                or latest.get("status")
                not in {"observed", "no_quote", "schema_error", "transport_error"}
                or latest.get("status") != acquisition.get("status")
                or latest.get("attempted_at") != acquisition.get("attempted_at")
            ):
                raise RuntimeError("source receipt is invalid")
            parse_stamp(str(latest["attempted_at"]))
            last_good = receipt.get("last_good")
            if last_good is not None:
                if not isinstance(last_good, dict):
                    raise RuntimeError("source receipt is invalid")
                parse_stamp(str(last_good["captured_at"]))
            operations = json.loads(
                (self.state_dir / "operations.json").read_text(encoding="utf-8")
            )
            maintenance = operations["maintenance"]
            if (
                not isinstance(operations, dict)
                or operations.get("schema_version") != 1
                or not isinstance(maintenance, dict)
                or maintenance.get("state") not in {"succeeded", "failed"}
            ):
                raise RuntimeError("maintenance receipt is invalid")
            parse_stamp(str(maintenance["observed_at"]))
            return {
                "state": "available",
                "date": receipt["date"],
                "sportsbook": {
                    "latest_attempted_at": latest["attempted_at"],
                    "latest_status": latest["status"],
                    "last_good_captured_at": last_good.get("captured_at") if last_good else None,
                },
                "maintenance": {
                    "observed_at": maintenance["observed_at"],
                    "state": maintenance["state"],
                },
            }
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("source receipt is unavailable") from exc

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(200, {"state": "live"})
            return
        if path == "/readiness":
            result = runtime_readiness(self.state_dir, heartbeat_seconds=180, job_lag_seconds=30)
            self._json(200 if result["state"] == "ready" else 503, result)
            return
        if path == "/source-health":
            try:
                self._json(200, self._source_health())
            except RuntimeError:
                self._json(503, {"state": "not_available"})
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
                manifest = PublicationCatalog.read_current(self.publication_dir)
                if not isinstance(manifest, dict):
                    raise RuntimeError("no accepted publication")
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
                if archive_date is not None:
                    payload = self._payload(body)
                    _validate_archive_metadata(row, body)
                    if payload.get("date") != archive_date:
                        raise RuntimeError("accepted archive payload belongs to another date")
                elif path == "/data/data.json":
                    payload = self._payload(body)
                    self._current_archive_row(
                        rows,
                        payload,
                        str(manifest["data_sha256"]),
                        body,
                    )
                elif path == "/data/release.json":
                    data_digest = manifest.get("data_sha256")
                    if not _is_digest(data_digest):
                        raise RuntimeError("accepted data digest is malformed")
                    data_body = _read_object(objects, data_digest, maximum)
                    payload = self._payload(data_body)
                    self._current_archive_row(rows, payload, data_digest, data_body)
                    receipt = self._payload_json(body, "accepted release receipt is malformed")
                    _validated_release(
                        receipt,
                        payload=payload,
                        payload_digest=hashlib.sha256(data_body).hexdigest(),
                    )
            except (
                EOFError,
                OSError,
                KeyError,
                StopIteration,
                TypeError,
                ValueError,
                RuntimeError,
                sqlite3.Error,
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

    @staticmethod
    def _payload_json(body: bytes, message: str) -> object:
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(message) from exc


def serve(
    *, web_dir: Path, publication_dir: Path, state_dir: Path, cache_dir: Path, host: str, port: int
) -> None:
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("host must be 127.0.0.1 or 0.0.0.0")
    handler = type(
        "ConfiguredRuntimeHandler",
        (RuntimeHandler,),
        {
            "web_dir": web_dir,
            "publication_dir": publication_dir,
            "state_dir": state_dir,
            "cache_dir": cache_dir,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
