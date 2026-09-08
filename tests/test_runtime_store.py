from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline
from ballpark.publication import canonical_json_bytes
from ballpark.runtime import JobContext
from ballpark.runtime_app import _accept_stage, _read_object
from ballpark.runtime_store import PublicationCatalog


def context(root: Path, token: str) -> JobContext:
    now = datetime.now(UTC)
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


def document(day: str) -> dict[str, object]:
    payload = DailyPipeline(ProjectPaths.discover()).build(
        datetime(2026, 8, 26, tzinfo=UTC).date(),
        fixture_path=Path("tests/fixtures/no_slate.json"),
        generated_at="2026-08-26T04:00:00Z",
    )
    payload["date"] = day
    payload["generated_at"] = f"{day}T04:00:00Z"
    return payload


def stage(root: Path, token: str, day: str) -> None:
    value = document(day)
    raw = canonical_json_bytes(value)
    digest = hashlib.sha256(raw).hexdigest()
    target = root / "publication" / ".staged" / token
    (target / "data").mkdir(parents=True)
    (target / "archive").mkdir()
    release = {
        "schema_version": 1,
        "date": day,
        "generated_at": value["generated_at"],
        "payload_sha256": digest,
        "status": "no_slate",
        "game_count": 0,
    }
    row = {
        "date": day,
        "generated_at": value["generated_at"],
        "payload_sha256": digest,
        "status": "no_slate",
        "game_count": 0,
    }
    (target / "data" / "data.json").write_bytes(raw)
    (target / "data" / "release.json").write_bytes(canonical_json_bytes(release))
    (target / "archive" / f"{day}.json").write_bytes(raw)
    (target / "archive" / "index.json").write_bytes(
        canonical_json_bytes(
            {"schema_version": 1, "updated_at": value["generated_at"], "dates": [row]}
        )
    )


def accept(root: Path, number: int) -> str:
    token = f"{number:032x}"
    stage(root, token, f"2026-09-{number + 1:02d}")
    _accept_stage(context(root, token))
    return token


def test_catalog_retains_accepts_and_private_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    sources = tmp_path / "cache" / "sources"
    sources.mkdir(parents=True)
    tokens = []
    for number in range(1, 4):
        (sources / "schedule.json").write_bytes(canonical_json_bytes({"observation": number}))
        tokens.append(accept(tmp_path, number))
    catalog = PublicationCatalog(tmp_path / "publication")
    manifests = [catalog.accepted(token) for token in tokens]
    assert all(manifests)
    assert catalog.current_manifest()["token"] == tokens[-1]
    assert len({item["input_receipts"]["schedule"] for item in manifests if item}) == 3


def test_catalog_rejects_corruption_and_bad_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    first = accept(tmp_path, 1)
    catalog = PublicationCatalog(tmp_path / "publication")
    manifest = catalog.accepted(first)
    assert manifest
    object_path = (
        tmp_path / "publication" / "objects" / f"{manifest['archive_index_sha256']}.json.gz"
    )
    object_path.write_bytes(gzip.compress(b'{"dates":"bad"}', mtime=0))
    stage(tmp_path, f"{2:032x}", "2026-09-03")
    with pytest.raises(RuntimeError):
        _accept_stage(context(tmp_path, f"{2:032x}"))
    assert catalog.current_manifest()["token"] == first
    root = tmp_path / "bad"
    token = f"{1:032x}"
    stage(root, token, "2026-09-02")
    target = root / "publication" / ".staged" / token / "data" / "data.json"
    bad = {
        "schema_version": 999,
        "date": "2026-09-02",
        "generated_at": "not-time",
        "status": "invented",
        "games": "xy",
    }
    target.write_bytes(canonical_json_bytes(bad))
    with pytest.raises(RuntimeError, match="schema"):
        _accept_stage(context(root, token))
    assert PublicationCatalog(root / "publication").current_manifest() is None


def test_catalog_archive_and_size_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    accept(tmp_path, 1)
    manifest = PublicationCatalog(tmp_path / "publication").current_manifest()
    assert manifest
    index_path = (
        tmp_path / "publication" / "objects" / f"{manifest['archive_index_sha256']}.json.gz"
    )
    index = json.loads(gzip.decompress(index_path.read_bytes()))
    assert index["dates"][0]["object_sha256"] == manifest["data_sha256"]
    objects = tmp_path / "objects"
    objects.mkdir()
    raw = b"{}\n"
    digest = hashlib.sha256(raw).hexdigest()
    (objects / f"{digest}.json.gz").write_bytes(
        gzip.compress(raw, mtime=0) + gzip.compress(b"", mtime=0) * 200
    )
    with pytest.raises(RuntimeError):
        _read_object(objects, digest, 128)


def test_catalog_1500_replay_is_flat_and_pending_queue_is_indexed(tmp_path: Path) -> None:
    catalog = PublicationCatalog(tmp_path / "publication")
    for number in range(1500):
        token = f"{number:032x}"
        manifest = {
            "schema_version": 3,
            "token": token,
            "data_sha256": "a" * 64,
            "release_sha256": "b" * 64,
            "archive_index_sha256": "c" * 64,
            "input_receipts": {},
            "accepted_at": "2026-09-08T00:00:00Z",
        }
        catalog.commit(token, manifest, {"a" * 64, "b" * 64, "c" * 64}, "2026-09-08T00:00:00Z")
    assert catalog.current_manifest()["token"] == f"{1499:032x}"
    catalog.register_object("d" * 64, "pending", "d.json.gz", "2026-09-08T00:00:00Z")
    assert catalog.maintenance("2027-01-01T00:00:00Z", 1) == [("d" * 64, "pending", "d.json.gz")]
