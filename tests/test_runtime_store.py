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

import ballpark.runtime_app as runtime_app
from ballpark.publication import canonical_json_bytes
from ballpark.runtime import JobContext
from ballpark.runtime_app import _accept_stage, _cleanup_store, _read_object
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


def _payload(publication_date: str) -> dict[str, object]:
    return {
        "date": publication_date,
        "generated_at": f"{publication_date}T04:00:00Z",
        "games": [],
        "status": "ready",
    }


def _release(payload: dict[str, object], digest: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "payload_sha256": digest,
        "status": payload["status"],
        "game_count": len(payload["games"]),
    }


def _index_row(payload: dict[str, object], digest: str) -> dict[str, object]:
    return {
        "date": payload["date"],
        "generated_at": payload["generated_at"],
        "payload_sha256": digest,
        "status": payload["status"],
        "game_count": len(payload["games"]),
    }


def _stage(root: Path, token: str, publication_date: str) -> None:
    stage = root / "publication" / ".staged" / token
    document = _payload(publication_date)
    payload = canonical_json_bytes(document)
    digest = hashlib.sha256(payload).hexdigest()
    (stage / "data").mkdir(parents=True)
    (stage / "archive").mkdir(parents=True)
    (stage / "data" / "data.json").write_bytes(payload)
    (stage / "data" / "release.json").write_bytes(canonical_json_bytes(_release(document, digest)))
    (stage / "archive" / f"{publication_date}.json").write_bytes(payload)
    (stage / "archive" / "index.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "updated_at": document["generated_at"],
                "dates": [_index_row(document, digest)],
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
    documents = {day: _payload(day) for day in ("2026-09-02", "2026-09-01")}
    payloads = {day: canonical_json_bytes(document) for day, document in documents.items()}
    for day, raw in payloads.items():
        (stage / "archive" / f"{day}.json").write_bytes(raw)
    current = payloads["2026-09-02"]
    current_digest = hashlib.sha256(current).hexdigest()
    (stage / "data" / "data.json").write_bytes(current)
    (stage / "data" / "release.json").write_bytes(
        canonical_json_bytes(_release(documents["2026-09-02"], current_digest))
    )
    (stage / "archive" / "index.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": 1,
                "updated_at": documents["2026-09-02"]["generated_at"],
                "dates": [
                    _index_row(document, hashlib.sha256(payloads[day]).hexdigest())
                    for day, document in documents.items()
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


def _mutate_archive_row(stage: Path) -> None:
    path = stage / "archive" / "index.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["dates"][0]["game_count"] = 999
    path.write_bytes(canonical_json_bytes(value))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda stage: _mutate_json(stage / "data" / "release.json", {"game_count": 999}),
            "receipt",
        ),
        (
            _mutate_archive_row,
            "archive",
        ),
    ],
)
def test_store_rejects_full_candidate_metadata_forgeries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: object, message: str
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    token = f"{1:032x}"
    _stage(tmp_path, token, "2026-09-02")
    stage = tmp_path / "publication" / ".staged" / token
    assert callable(mutate)
    mutate(stage)
    with pytest.raises(RuntimeError, match=message):
        _accept_stage(_context(tmp_path, token))
    assert not (tmp_path / "publication" / "pointer.json").exists()
    assert not (tmp_path / "publication" / "releases" / token).exists()


def _mutate_json(path: Path, update: dict[str, object]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(update)
    path.write_bytes(canonical_json_bytes(value))


def test_store_reclaims_expired_unaccepted_candidate_without_touching_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    first = _accept(tmp_path, monkeypatch, 1)
    publication = tmp_path / "publication"
    pointer_before = (publication / "pointer.json").read_bytes()
    failed = f"{2:032x}"
    _stage(tmp_path, failed, "2026-09-03")
    calls = 0

    def disk_usage(_path: Path) -> object:
        nonlocal calls
        calls += 1
        return type("Usage", (), {"free": 0 if calls >= 3 else 1_000})()

    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "100")
    monkeypatch.setattr("ballpark.runtime_app.shutil.disk_usage", disk_usage)
    with pytest.raises(RuntimeError, match="reserve"):
        _accept_stage(_context(tmp_path, failed))
    assert (publication / "pointer.json").read_bytes() == pointer_before
    assert (publication / "releases" / failed / "manifest.json").is_file()
    assert not (publication / "releases" / failed / "accepted.json").exists()
    monkeypatch.undo()
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    third = _accept(tmp_path, monkeypatch, 3)
    assert not (publication / "releases" / failed).exists()
    assert (publication / "releases" / first / "accepted.json").is_file()
    assert (publication / "releases" / third / "accepted.json").is_file()


def test_read_object_enforces_compressed_and_decoded_limits(tmp_path: Path) -> None:
    objects = tmp_path / "objects"
    objects.mkdir()
    raw = b"{}\n"
    digest = hashlib.sha256(raw).hexdigest()
    (objects / f"{digest}.json.gz").write_bytes(
        gzip.compress(raw, mtime=0) + gzip.compress(b"", mtime=0) * 200
    )
    with pytest.raises(RuntimeError, match="size limit"):
        _read_object(objects, digest, 128)
    oversized = b"x" * 129
    oversized_digest = hashlib.sha256(oversized).hexdigest()
    (objects / f"{oversized_digest}.json.gz").write_bytes(gzip.compress(oversized, mtime=0))
    with pytest.raises(RuntimeError, match="size limit"):
        _read_object(objects, oversized_digest, 128)


@pytest.mark.parametrize("failed_name", ["pointer.json", "accepted.json"])
def test_pointer_authority_recovers_marker_and_pointer_write_interruptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_name: str
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    first = _accept(tmp_path, monkeypatch, 1)
    publication = tmp_path / "publication"
    interrupted = f"{2:032x}"
    _stage(tmp_path, interrupted, "2026-09-03")
    original_write = runtime_app.atomic_write

    def interrupt(path: Path, content: bytes) -> None:
        if path.name == failed_name:
            raise OSError("simulated interruption")
        original_write(path, content)

    monkeypatch.setattr(runtime_app, "atomic_write", interrupt)
    with pytest.raises(OSError, match="interruption"):
        _accept_stage(_context(tmp_path, interrupted))
    monkeypatch.setattr(runtime_app, "atomic_write", original_write)

    if failed_name == "pointer.json":
        assert first in (publication / "pointer.json").read_text(encoding="utf-8")
        assert not (publication / "releases" / interrupted / "accepted.json").exists()
    else:
        assert interrupted in (publication / "pointer.json").read_text(encoding="utf-8")
        assert not (publication / "releases" / interrupted / "accepted.json").exists()

    third = _accept(tmp_path, monkeypatch, 3)
    assert (publication / "releases" / third / "accepted.json").is_file()
    if failed_name == "pointer.json":
        assert not (publication / "releases" / interrupted).exists()
    else:
        assert (publication / "releases" / interrupted / "manifest.json").is_file()
        _cleanup_store(
            publication,
            current=third,
            previous=None,
            maximum=32 * 1024 * 1024,
        )
        assert (publication / "releases" / interrupted / "manifest.json").is_file()
