from __future__ import annotations

import gzip
import hashlib
import json
import threading
from datetime import date, timedelta
from functools import lru_cache
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline
from ballpark.publication import canonical_json_bytes
from ballpark.runtime_server import RuntimeHandler
from ballpark.runtime_store import PublicationCatalog


def _store(publication: Path, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    objects = publication / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    (objects / f"{digest}.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    return digest


@lru_cache
def _template() -> dict[str, object]:
    return DailyPipeline(ProjectPaths.discover()).build(
        date(2026, 8, 26),
        fixture_path=Path("tests/fixtures/no_slate.json"),
        generated_at="2026-08-26T04:00:00Z",
    )


def _payload(day: date) -> dict[str, object]:
    value = _template().copy()
    value["date"] = day.isoformat()
    value["generated_at"] = f"{day.isoformat()}T04:00:00Z"
    return value


def _install(publication: Path, payloads: list[dict[str, object]]) -> tuple[str, dict[str, bytes]]:
    rows: list[dict[str, object]] = []
    bodies: dict[str, bytes] = {}
    digests: set[str] = set()
    for payload in payloads:
        body = canonical_json_bytes(payload)
        digest = _store(publication, body)
        day = str(payload["date"])
        bodies[day] = body
        digests.add(digest)
        rows.append(
            {
                "date": day,
                "generated_at": payload["generated_at"],
                "payload_sha256": digest,
                "object_sha256": digest,
                "status": payload["status"],
                "game_count": len(payload["games"]),
            }
        )
    current = payloads[-1]
    current_body = bodies[str(current["date"])]
    data_digest = hashlib.sha256(current_body).hexdigest()
    release = {
        "schema_version": 1,
        "date": current["date"],
        "generated_at": current["generated_at"],
        "status": current["status"],
        "game_count": len(current["games"]),
        "payload_sha256": data_digest,
    }
    release_digest = _store(publication, canonical_json_bytes(release))
    index_digest = _store(
        publication,
        canonical_json_bytes(
            {
                "schema_version": 2,
                "dates": sorted(rows, key=lambda row: str(row["date"]), reverse=True),
            }
        ),
    )
    digests.update({release_digest, index_digest})
    token = "f" * 32
    manifest = {
        "schema_version": 3,
        "token": token,
        "data_sha256": data_digest,
        "release_sha256": release_digest,
        "archive_index_sha256": index_digest,
        "input_receipts": {},
    }
    PublicationCatalog(publication).commit(token, manifest, digests, "2026-09-08T00:00:00Z")
    return token, bodies


class _Server:
    def __init__(self, root: Path) -> None:
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            type(
                "TestRuntimeHandler",
                (RuntimeHandler,),
                {
                    "web_dir": root / "web",
                    "publication_dir": root / "publication",
                    "state_dir": root / "state",
                },
            ),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def get(self, path: str) -> tuple[int, bytes]:
        with urlopen(f"http://127.0.0.1:{self.server.server_port}{path}") as response:
            return response.status, response.read()


def test_runtime_http_fails_closed_for_hash_valid_schema_invalid_current_and_history(
    tmp_path: Path,
) -> None:
    invalid = {
        "schema_version": 999,
        "date": "2026-09-08",
        "generated_at": "2026-09-08T04:00:00Z",
        "status": "invented",
        "games": "bad",
    }
    _install(tmp_path / "publication", [invalid])
    with _Server(tmp_path) as server:
        for path in ("/data/data.json", "/archive/2026-09-08.json"):
            try:
                server.get(path)
            except Exception as exc:
                assert getattr(exc, "code", None) == 404
                assert json.loads(exc.read()) == {"state": "not_published"}
            else:
                raise AssertionError(f"{path} served an invalid accepted payload")


def test_runtime_http_reads_valid_thirteen_date_catalog_without_history_rescan(
    tmp_path: Path,
) -> None:
    start = date(2026, 8, 1)
    payloads = [_payload(start + timedelta(days=offset)) for offset in range(13)]
    _token, bodies = _install(tmp_path / "publication", payloads)
    with _Server(tmp_path) as server:
        status, current = server.get("/data/data.json")
        assert status == 200
        assert current == bodies["2026-08-13"]
        status, release = server.get("/data/release.json")
        assert status == 200
        assert json.loads(release)["payload_sha256"] == hashlib.sha256(current).hexdigest()
        status, index = server.get("/archive/index.json")
        assert status == 200
        assert len(json.loads(index)["dates"]) == 13
        for day, body in bodies.items():
            status, historical = server.get(f"/archive/{day}.json")
            assert status == 200
            assert historical == body
