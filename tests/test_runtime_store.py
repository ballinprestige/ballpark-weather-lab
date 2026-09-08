from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from ballpark.publication import canonical_json_bytes
from ballpark.runtime import JobContext
from ballpark.runtime_app import _accept_stage, _cleanup_store
from ballpark.runtime_server import RuntimeHandler


def _context(root: Path, token: str) -> JobContext:
    now = datetime(2026, 9, 8, 4, 0, tzinfo=UTC)
    return JobContext(
        "publish",
        now,
        now,
        now + timedelta(seconds=30),
        1,
        token,
        root / "state",
        root / "cache",
        root / "publication",
    )


def _stage(root: Path, token: str, publication_date: str) -> None:
    stage = root / "publication" / ".staged" / token
    payload = canonical_json_bytes({"date": publication_date, "games": [], "status": "ready"})
    digest = hashlib.sha256(payload).hexdigest()
    (stage / "data").mkdir(parents=True)
    (stage / "archive").mkdir(parents=True)
    (stage / "data" / "data.json").write_bytes(payload)
    (stage / "data" / "release.json").write_bytes(
        canonical_json_bytes({"date": publication_date, "payload_sha256": digest})
    )
    (stage / "archive" / f"{publication_date}.json").write_bytes(payload)
    (stage / "archive" / "index.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "dates": [{"date": publication_date, "payload_sha256": digest}],
            }
        )
    )


def _accept(root: Path, monkeypatch: pytest.MonkeyPatch, number: int) -> str:
    token = f"{number:032x}"
    _stage(root, token, f"2026-09-{number + 1:02d}")
    _accept_stage(_context(root, token))
    return token


@contextmanager
def _server(root: Path):
    web = root / "web"
    web.mkdir(exist_ok=True)
    handler = type(
        "TestRuntimeHandler",
        (RuntimeHandler,),
        {"web_dir": web, "publication_dir": root / "publication", "state_dir": root / "state"},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_store_rejects_corrupt_prior_index_without_advancing_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    first = _accept(tmp_path, monkeypatch, 1)
    publication = tmp_path / "publication"
    pointer_before = (publication / "pointer.json").read_bytes()
    manifest = json.loads((publication / "releases" / first / "manifest.json").read_text())
    index_object = publication / "objects" / f"{manifest['archive_index_sha256']}.json.gz"
    index_object.write_bytes(gzip.compress(b'{"dates":"corrupt"}', mtime=0))
    _stage(tmp_path, f"{2:032x}", "2026-09-03")
    with pytest.raises(RuntimeError, match="archive"):
        _accept_stage(_context(tmp_path, f"{2:032x}"))
    assert (publication / "pointer.json").read_bytes() == pointer_before


def test_http_rejects_escape_tokens_invalid_dates_and_unindexed_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    _accept(tmp_path, monkeypatch, 1)
    publication = tmp_path / "publication"
    with _server(tmp_path) as base:
        assert _status(f"{base}/archive/xxxx-xx-xx.json") == 404
        assert _status(f"{base}/archive/2026-09-08.json") == 404
        assert _status(f"{base}/data/data.json") == 200
    (publication / "pointer.json").write_text(
        json.dumps({"current": {"token": "../outside"}}), encoding="utf-8"
    )
    with _server(tmp_path) as base:
        assert _status(f"{base}/data/data.json") == 404


def test_store_deduplicates_inputs_and_reclaims_only_orphaned_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    sources = tmp_path / "cache" / "sources"
    sources.mkdir(parents=True)
    source = canonical_json_bytes({"date": "2026-09-02", "source": "durable"})
    (sources / "schedule.json").write_bytes(source)
    first = _accept(tmp_path, monkeypatch, 1)
    second = _accept(tmp_path, monkeypatch, 2)
    publication = tmp_path / "publication"
    manifests = [
        json.loads((publication / "releases" / token / "manifest.json").read_text())
        for token in (first, second)
    ]
    assert manifests[0]["input_receipts"]["schedule"] == manifests[1]["input_receipts"]["schedule"]
    input_object = publication / "objects" / f"{manifests[1]['input_receipts']['schedule']}.json.gz"
    assert input_object.is_file()
    for number in range(3, 7):
        _accept(tmp_path, monkeypatch, number)
    retained = list((publication / "releases").iterdir())
    assert len(retained) == 6

    active, orphan = f"{6:032x}", f"{7:032x}"
    (publication / ".staged" / active).mkdir(parents=True)
    (publication / ".staged" / orphan).mkdir(parents=True)
    os.utime(publication / ".staged" / orphan, (1, 1))
    _cleanup_store(
        publication,
        current=active,
        previous={"token": f"{5:032x}"},
        maximum=32 * 1024 * 1024,
    )
    assert (publication / ".staged" / active).is_dir()
    assert not (publication / ".staged" / orphan).exists()


def test_store_retains_every_accepted_manifest_data_and_unique_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    sources = tmp_path / "cache" / "sources"
    sources.mkdir(parents=True)
    tokens: list[str] = []
    for number in (1, 2, 3):
        raw = canonical_json_bytes({"date": "2026-09-02", "source_observation": number})
        (sources / "schedule.json").write_bytes(raw)
        tokens.append(_accept(tmp_path, monkeypatch, number))
    publication = tmp_path / "publication"
    manifests = [
        json.loads((publication / "releases" / token / "manifest.json").read_text())
        for token in tokens
    ]
    assert len(list((publication / "releases").iterdir())) == 3
    assert len({manifest["input_receipts"]["schedule"] for manifest in manifests}) == 3
    for manifest in manifests:
        assert (publication / "objects" / f"{manifest['data_sha256']}.json.gz").is_file()
        assert (
            publication / "objects" / f"{manifest['input_receipts']['schedule']}.json.gz"
        ).is_file()


def test_store_rejects_mismatched_candidate_receipt_without_switching_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    first = _accept(tmp_path, monkeypatch, 1)
    publication = tmp_path / "publication"
    pointer_before = (publication / "pointer.json").read_bytes()
    token = f"{2:032x}"
    _stage(tmp_path, token, "2026-09-03")
    receipt_path = publication / ".staged" / token / "data" / "release.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["payload_sha256"] = "0" * 64
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(RuntimeError, match="receipt"):
        _accept_stage(_context(tmp_path, token))
    assert first in (publication / "pointer.json").read_text()
    assert (publication / "pointer.json").read_bytes() == pointer_before


def test_store_imports_all_indexed_archive_objects_into_empty_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    token = f"{1:032x}"
    publication = tmp_path / "publication"
    stage = publication / ".staged" / token
    (stage / "data").mkdir(parents=True)
    (stage / "archive").mkdir(parents=True)
    payloads = {
        "2026-09-02": canonical_json_bytes({"date": "2026-09-02", "games": [], "status": "ready"}),
        "2026-09-01": canonical_json_bytes({"date": "2026-09-01", "games": [], "status": "ready"}),
    }
    for day, raw in payloads.items():
        (stage / "archive" / f"{day}.json").write_bytes(raw)
    current = payloads["2026-09-02"]
    (stage / "data" / "data.json").write_bytes(current)
    (stage / "data" / "release.json").write_bytes(
        canonical_json_bytes(
            {"date": "2026-09-02", "payload_sha256": hashlib.sha256(current).hexdigest()}
        )
    )
    (stage / "archive" / "index.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "dates": [
                    {"date": day, "payload_sha256": hashlib.sha256(raw).hexdigest()}
                    for day, raw in payloads.items()
                ],
            }
        )
    )
    _accept_stage(_context(tmp_path, token))
    manifest = json.loads((publication / "releases" / token / "manifest.json").read_text())
    imported = json.loads(
        gzip.decompress(
            (publication / "objects" / f"{manifest['archive_index_sha256']}.json.gz").read_bytes()
        )
    )
    assert {row["payload_sha256"] for row in imported["dates"]} == {
        hashlib.sha256(raw).hexdigest() for raw in payloads.values()
    }
    assert all(
        (publication / "objects" / f"{row['object_sha256']}.json.gz").is_file()
        for row in imported["dates"]
    )
