from __future__ import annotations

# ruff: noqa: E501
import gzip
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pytest

import ballpark.runtime_store as runtime_store
from ballpark.paths import ProjectPaths
from ballpark.pipeline import DailyPipeline
from ballpark.publication import canonical_json_bytes
from ballpark.runtime import JobContext, JobSpec, RuntimeWorker
from ballpark.runtime_app import (
    _accept_stage,
    _catalog_acceptance_reconciled,
    _read_object,
    maintain_publication_store,
)
from ballpark.runtime_store import PublicationCatalog


def context(
    root: Path,
    token: str,
    *,
    now: datetime | None = None,
    deadline_seconds: int = 30,
    clock: Callable[[], datetime] | None = None,
) -> JobContext:
    now = now or datetime.now(UTC)
    return JobContext(
        "publish",
        now,
        now,
        now + timedelta(seconds=deadline_seconds),
        1,
        token,
        root / "state",
        root / "cache",
        root / "publication",
        clock,
    )


@lru_cache
def _template() -> dict[str, object]:
    return DailyPipeline(ProjectPaths.discover()).build(
        datetime(2026, 8, 26, tzinfo=UTC).date(),
        fixture_path=Path("tests/fixtures/no_slate.json"),
        generated_at="2026-08-26T04:00:00Z",
    )


def document(day: str) -> dict[str, object]:
    payload = _template().copy()
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


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def registered_stage(root: Path, ctx: JobContext, day: str) -> PublicationCatalog:
    catalog = PublicationCatalog(ctx.publication_dir)
    candidate = ctx.publication_dir / ".staged" / ctx.token
    catalog.register_candidate(ctx.token, candidate, stamp(ctx.started_at), stamp(ctx.deadline_at))
    catalog.register_stage_paths(
        ctx.token,
        {"data/data.json", "data/release.json", f"archive/{day}.json", "archive/index.json"},
        stamp(ctx.started_at),
    )
    stage(root, ctx.token, day)
    return catalog


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
    catalog.register_candidate(
        "pending",
        tmp_path / "publication" / ".staged" / "pending",
        "2026-09-08T00:00:00Z",
        "2026-09-08T00:00:00Z",
    )
    catalog.register_object("d" * 64, "pending", "d.json.gz", "2026-09-08T00:00:00Z")
    assert catalog.maintenance("2027-01-01T00:00:00Z", 1) == [("d" * 64, "pending", "d.json.gz")]


def test_same_token_retry_is_idempotent_after_its_original_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    started = datetime(2026, 9, 8, 8, tzinfo=UTC)
    clock = MutableClock(started)
    token = f"{1:032x}"
    stage(tmp_path, token, "2026-09-02")
    first = context(tmp_path, token, now=started, deadline_seconds=1, clock=clock)
    _accept_stage(first)
    catalog = PublicationCatalog(tmp_path / "publication")
    accepted = catalog.accepted(token)
    clock.value = started + timedelta(seconds=2)
    _accept_stage(first)
    assert catalog.accepted(token) == accepted


def test_catalog_commit_samples_default_clock_after_real_lock_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default UTC clock is read only after BEGIN IMMEDIATE owns the lock."""
    catalog = PublicationCatalog(tmp_path / "publication")
    token = f"{2:032x}"
    started = datetime.now(UTC)
    manifest = {"schema_version": 3, "token": token, "data_sha256": "a" * 64}
    catalog.register_candidate(
        token,
        tmp_path / "publication" / ".staged" / token,
        stamp(started),
        stamp(started + timedelta(seconds=1)),
    )
    catalog.begin_accept(token)
    catalog.set_candidate_manifest(token, manifest)

    sampled = threading.Event()
    original_stamp = runtime_store._stamp

    def mark_default_sample(value: datetime | None = None) -> str:
        sampled.set()
        return original_stamp(value)

    monkeypatch.setattr(runtime_store, "_stamp", mark_default_sample)
    lock = sqlite3.connect(catalog.path, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    errors: list[Exception] = []

    def commit() -> None:
        try:
            catalog.commit(token, manifest, {"a" * 64})
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=commit)
    worker.start()
    try:
        assert not sampled.wait(0.1)
        time.sleep(1.05)
    finally:
        lock.execute("COMMIT")
        lock.close()
    worker.join(5)

    assert sampled.is_set()
    assert len(errors) == 1 and isinstance(errors[0], TimeoutError)
    assert catalog.current_manifest() is None
    database = sqlite3.connect(catalog.path)
    try:
        state, error = database.execute(
            "SELECT state,error FROM pending_candidates WHERE token=?", (token,)
        ).fetchone()
    finally:
        database.close()
    assert state == "failed" and "deadline elapsed" in error


def test_catalog_deadlines_compare_legacy_whole_seconds_at_subsecond_precision(
    tmp_path: Path,
) -> None:
    catalog = PublicationCatalog(tmp_path / "publication")
    token = f"{5:032x}"
    started = datetime(2026, 9, 8, 8, tzinfo=UTC)
    deadline = "2026-09-08T08:00:01Z"
    manifest = {"schema_version": 3, "token": token, "data_sha256": "a" * 64}
    catalog.register_candidate(
        token,
        tmp_path / "publication" / ".staged" / token,
        stamp(started),
        deadline,
    )
    catalog.begin_accept(token, now=stamp(started + timedelta(milliseconds=900)))
    catalog.set_candidate_manifest(token, manifest)

    with pytest.raises(TimeoutError, match="deadline elapsed"):
        catalog.commit(
            token,
            manifest,
            {"a" * 64},
            clock=lambda: started + timedelta(seconds=1, milliseconds=100),
        )
    assert catalog.current_manifest() is None


def test_subsecond_active_candidate_skips_maintenance_cleanup(tmp_path: Path) -> None:
    catalog = PublicationCatalog(tmp_path / "publication")
    token, digest = f"{6:032x}", "b" * 64
    started = datetime(2026, 9, 8, 8, tzinfo=UTC)
    catalog.register_candidate(
        token,
        tmp_path / "publication" / ".staged" / token,
        stamp(started),
        stamp(started + timedelta(seconds=1, milliseconds=500)),
    )
    catalog.register_object(digest, token, f"{digest}.json.gz", stamp(started))
    removed: list[str] = []

    outcome = catalog.maintain(
        now=stamp(started + timedelta(seconds=1)),
        cutoff=stamp(started + timedelta(seconds=1)),
        entry_limit=1,
        byte_limit=1,
        time_limit_seconds=1,
        remove=lambda _kind, key, _relative, _remaining: (removed.append(key) is not None, 1, None),
    )

    assert outcome["state"] == "skipped_active"
    assert not removed


def test_actual_acceptor_rechecks_callable_clock_after_real_lock_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    started = datetime(2026, 9, 8, 8, tzinfo=UTC)
    clock = MutableClock(started)
    current, expired = f"{3:032x}", f"{4:032x}"
    stage(tmp_path, current, "2026-09-08")
    _accept_stage(context(tmp_path, current, now=started, clock=clock))

    stage(tmp_path, expired, "2026-09-09")
    expired_context = context(tmp_path, expired, now=started, deadline_seconds=1, clock=clock)
    original = PublicationCatalog.set_candidate_manifest
    manifest_ready, allow_commit = threading.Event(), threading.Event()

    def pause_after_manifest(
        self: PublicationCatalog, candidate_token: str, manifest: dict[str, object]
    ) -> None:
        original(self, candidate_token, manifest)
        if candidate_token == expired:
            manifest_ready.set()
            assert allow_commit.wait(5)

    monkeypatch.setattr(PublicationCatalog, "set_candidate_manifest", pause_after_manifest)
    errors: list[Exception] = []

    def accept_expired() -> None:
        try:
            _accept_stage(expired_context)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=accept_expired)
    worker.start()
    assert manifest_ready.wait(5)
    lock = sqlite3.connect(tmp_path / "publication" / "catalog.sqlite3", isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    clock.value = started + timedelta(seconds=2)
    allow_commit.set()
    time.sleep(0.1)
    lock.execute("COMMIT")
    lock.close()
    worker.join(5)

    catalog = PublicationCatalog(tmp_path / "publication")
    assert len(errors) == 1 and isinstance(errors[0], TimeoutError)
    assert catalog.current_manifest()["token"] == current
    database = sqlite3.connect(catalog.path)
    try:
        candidate = database.execute(
            "SELECT state,error FROM pending_candidates WHERE token=?", (expired,)
        ).fetchone()
        publication_state = database.execute(
            "SELECT current_token,rollback_token FROM publication_state WHERE id=1"
        ).fetchone()
    finally:
        database.close()
    assert candidate[0] == "failed" and "deadline elapsed" in candidate[1]
    assert publication_state == (current, None)


def test_postcommit_interruption_reconciles_on_same_token_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    token = f"{1:032x}"
    stage(tmp_path, token, "2026-09-02")
    original = PublicationCatalog.commit

    def commit_then_interrupt(self: PublicationCatalog, *args: object, **kwargs: object) -> bool:
        original(self, *args, **kwargs)
        raise OSError("after catalog commit")

    monkeypatch.setattr(PublicationCatalog, "commit", commit_then_interrupt)
    _accept_stage(context(tmp_path, token))
    monkeypatch.setattr(PublicationCatalog, "commit", original)
    _accept_stage(context(tmp_path, token))
    assert PublicationCatalog(tmp_path / "publication").current_manifest()["token"] == token


def test_real_acceptor_replays_1500_minute_publications(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    sources = tmp_path / "cache" / "sources"
    sources.mkdir(parents=True)
    for number in range(1500):
        token = f"{number:032x}"
        stage(tmp_path, token, "2026-09-08")
        (sources / "schedule.json").write_bytes(canonical_json_bytes({"minute": number}))
        _accept_stage(context(tmp_path, token))
    catalog = PublicationCatalog(tmp_path / "publication")
    assert catalog.current_manifest()["token"] == f"{1499:032x}"
    assert sum(catalog.accepted(f"{number:032x}") is not None for number in range(1500)) == 1500


def test_catalog_migrates_known_schema_without_discarding_pending_evidence(tmp_path: Path) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    database = sqlite3.connect(publication / "catalog.sqlite3")
    database.executescript(
        """
        CREATE TABLE accepted_releases (token TEXT PRIMARY KEY, manifest TEXT NOT NULL, accepted_at TEXT NOT NULL);
        CREATE TABLE publication_state (id INTEGER PRIMARY KEY CHECK(id=1), current_token TEXT, rollback_token TEXT);
        CREATE TABLE protected_objects (digest TEXT PRIMARY KEY, token TEXT NOT NULL);
        CREATE TABLE pending_candidates (token TEXT PRIMARY KEY, stage_path TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT);
        CREATE TABLE pending_objects (digest TEXT PRIMARY KEY, token TEXT NOT NULL, relative_path TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE catalog_errors (id INTEGER PRIMARY KEY, token TEXT, message TEXT NOT NULL, created_at TEXT NOT NULL);
        """
    )
    token, digest, created = f"{1:032x}", "d" * 64, "2026-09-08T00:00:00Z"
    database.execute(
        "INSERT INTO pending_candidates(token,stage_path,created_at,error) VALUES(?,?,?,NULL)",
        (token, str(publication / ".staged" / token), created),
    )
    database.execute(
        "INSERT INTO pending_objects VALUES(?,?,?,?)", (digest, token, f"{digest}.json.gz", created)
    )
    database.commit()
    database.close()

    catalog = PublicationCatalog(publication)
    database = sqlite3.connect(catalog.path)
    try:
        assert database.execute("PRAGMA user_version").fetchone()[0] == 3
        assert database.execute(
            "SELECT state,deadline_at FROM pending_candidates WHERE token=?", (token,)
        ).fetchone() == ("preparing", created)
        assert database.execute(
            "SELECT token,digest FROM pending_object_refs"
        ).fetchone() == (token, digest)
    finally:
        database.close()


def test_unknown_catalog_schema_fails_before_mutation(tmp_path: Path) -> None:
    publication = tmp_path / "publication"
    publication.mkdir()
    database = sqlite3.connect(publication / "catalog.sqlite3")
    database.execute("CREATE TABLE unexpected_format (value TEXT)")
    database.commit()
    database.close()
    with pytest.raises(RuntimeError, match="unknown"):
        PublicationCatalog(publication)
    database = sqlite3.connect(publication / "catalog.sqlite3")
    try:
        assert database.execute("SELECT name FROM sqlite_master WHERE name='unexpected_format'").fetchone()
        assert database.execute("PRAGMA user_version").fetchone()[0] == 0
    finally:
        database.close()


def test_active_candidate_skips_cleanup_then_expired_candidate_is_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    observed = datetime(2030, 1, 1, tzinfo=UTC)
    token, digest = f"{2:032x}", "e" * 64
    publication = tmp_path / "publication"
    candidate = publication / ".staged" / token
    (candidate / "data").mkdir(parents=True)
    (publication / "objects").mkdir()
    (candidate / "data" / "data.json").write_bytes(b"stage")
    object_path = publication / "objects" / f"{digest}.json.gz"
    object_path.write_bytes(b"object")
    catalog = PublicationCatalog(publication)
    catalog.register_candidate(
        token, candidate, stamp(observed - timedelta(minutes=2)), stamp(observed + timedelta(minutes=1))
    )
    catalog.register_stage_paths(token, {"data/data.json"}, stamp(observed - timedelta(minutes=2)))
    catalog.register_object(digest, token, object_path.name, stamp(observed - timedelta(minutes=2)))

    skipped = maintain_publication_store(publication, now=observed)
    assert skipped["state"] == "skipped_active"
    assert object_path.exists() and (candidate / "data" / "data.json").exists()

    reclaimed = maintain_publication_store(publication, now=observed + timedelta(minutes=2))
    assert reclaimed["deleted"] == 2
    assert not object_path.exists()
    assert not (candidate / "data" / "data.json").exists()


def test_reused_pending_digest_is_protected_and_pending_metadata_is_cleared(tmp_path: Path) -> None:
    catalog = PublicationCatalog(tmp_path / "publication")
    now = datetime.now(UTC)
    old, new, digest = f"{3:032x}", f"{4:032x}", "f" * 64
    object_path = tmp_path / "publication" / "objects" / f"{digest}.json.gz"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"private bytes")
    catalog.register_candidate(
        old,
        tmp_path / "publication" / ".staged" / old,
        stamp(now - timedelta(hours=2)),
        stamp(now - timedelta(hours=1)),
    )
    catalog.register_object(digest, old, object_path.name, stamp(now - timedelta(hours=2)))
    catalog.register_candidate(
        new,
        tmp_path / "publication" / ".staged" / new,
        stamp(now),
        stamp(now + timedelta(minutes=5)),
    )
    catalog.begin_accept(new)
    catalog.register_object(digest, new, object_path.name, stamp(now))
    manifest = {"token": new, "data_sha256": digest}
    catalog.set_candidate_manifest(new, manifest)
    catalog.commit(new, manifest, {digest}, stamp(now))

    database = sqlite3.connect(catalog.path)
    try:
        assert database.execute("SELECT COUNT(*) FROM pending_objects").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM pending_object_refs").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM protected_objects WHERE digest=?", (digest,)).fetchone()[0]
    finally:
        database.close()
    outcome = maintain_publication_store(tmp_path / "publication", now=now + timedelta(hours=2))
    assert outcome["deleted"] == 0
    assert object_path.read_bytes() == b"private bytes"


def test_cleanup_holds_catalog_fence_until_deletion_finishes(tmp_path: Path) -> None:
    catalog = PublicationCatalog(tmp_path / "publication")
    now = datetime.now(UTC)
    old, new, digest = f"{5:032x}", f"{6:032x}", "a" * 64
    catalog.register_candidate(
        old,
        tmp_path / "publication" / ".staged" / old,
        stamp(now - timedelta(hours=2)),
        stamp(now - timedelta(hours=1)),
    )
    catalog.register_object(digest, old, f"{digest}.json.gz", stamp(now - timedelta(hours=2)))
    deletion_started, release_deletion, maintenance_done = threading.Event(), threading.Event(), threading.Event()
    registration_done = threading.Event()
    result: dict[str, object] = {}

    def remove(_kind: str, _key: str, _relative: str, _remaining: int) -> tuple[bool, int, str | None]:
        deletion_started.set()
        assert release_deletion.wait(2)
        return True, 1, None

    def run_maintenance() -> None:
        result.update(
            catalog.maintain(
                now=stamp(now),
                cutoff=stamp(now),
                entry_limit=1,
                byte_limit=10,
                time_limit_seconds=2,
                remove=remove,
            )
        )
        maintenance_done.set()

    def register_new_candidate() -> None:
        PublicationCatalog(tmp_path / "publication").register_candidate(
            new,
            tmp_path / "publication" / ".staged" / new,
            stamp(now),
            stamp(now + timedelta(minutes=5)),
        )
        registration_done.set()

    cleaner = threading.Thread(target=run_maintenance)
    cleaner.start()
    assert deletion_started.wait(2)
    contender = threading.Thread(target=register_new_candidate)
    contender.start()
    time.sleep(0.1)
    assert not registration_done.is_set()
    release_deletion.set()
    cleaner.join(2)
    contender.join(2)
    assert maintenance_done.is_set() and registration_done.is_set()
    assert result["deleted"] == 1


def test_accepted_stage_cleanup_keeps_accepted_private_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    now = datetime.now(UTC)
    token, day = f"{7:032x}", "2026-09-02"
    ctx = context(tmp_path, token, now=now)
    registered_stage(tmp_path, ctx, day)
    sources = tmp_path / "cache" / "sources"
    sources.mkdir(parents=True)
    (sources / "private.json").write_bytes(canonical_json_bytes({"private": True}))
    _accept_stage(ctx)
    catalog = PublicationCatalog(ctx.publication_dir)
    manifest = catalog.accepted(token)
    assert manifest
    private_object = ctx.publication_dir / "objects" / f"{manifest['input_receipts']['private']}.json.gz"
    assert private_object.is_file()
    # A recovered accepted candidate can still carry stale pending metadata;
    # maintenance must clear it without touching the protected bytes.
    catalog.register_object(
        manifest["input_receipts"]["private"], token, private_object.name, stamp(now)
    )
    outcome = maintain_publication_store(
        ctx.publication_dir, now=ctx.deadline_at + timedelta(seconds=1)
    )
    assert outcome["deleted"] >= 4
    assert outcome["metadata_cleared"] == 1
    assert not (ctx.publication_dir / ".staged" / token).exists()
    assert private_object.is_file()
    assert catalog.accepted(token) == manifest


def test_stage_cleanup_leaves_unregistered_bytes_and_reports_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    now = datetime.now(UTC)
    token, day = f"{10:032x}", "2026-09-03"
    ctx = context(tmp_path, token, now=now)
    registered_stage(tmp_path, ctx, day)
    unknown = ctx.publication_dir / ".staged" / token / "unregistered.txt"
    unknown.write_bytes(b"retain me")
    _accept_stage(ctx)

    outcome = maintain_publication_store(
        ctx.publication_dir, now=ctx.deadline_at + timedelta(seconds=1)
    )
    assert outcome["reason"] == "unknown_stage_entries"
    assert unknown.read_bytes() == b"retain me"


def test_cleanup_grace_and_entry_byte_time_budgets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    publication = tmp_path / "publication"
    catalog = PublicationCatalog(publication)
    observed = datetime(2030, 1, 1, tzinfo=UTC)
    for number in range(3):
        token = f"{number + 8:032x}"
        digest = f"{number + 1:x}" * 64
        catalog.register_candidate(
            token,
            publication / ".staged" / token,
            stamp(observed - timedelta(hours=2)),
            stamp(observed - timedelta(hours=1)),
        )
        catalog.register_object(digest, token, f"{digest}.json.gz", stamp(observed - timedelta(hours=2)))
        path = publication / "objects" / f"{digest}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"four")

    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "10800")
    assert maintain_publication_store(publication, now=observed)["deleted"] == 0

    monkeypatch.setenv("BALLPARK_STORE_GRACE_SECONDS", "0")
    monkeypatch.setenv("BALLPARK_CLEANUP_BATCH", "2")
    monkeypatch.setenv("BALLPARK_CLEANUP_MAX_BYTES", "8")
    bounded = maintain_publication_store(publication, now=observed)
    assert bounded["inspected"] == 2
    assert bounded["deleted"] == 2
    assert bounded["bytes"] == 8

    monkeypatch.setenv("BALLPARK_CLEANUP_MAX_BYTES", "1")
    byte_limited = maintain_publication_store(publication, now=observed)
    assert byte_limited["state"] == "budget"
    assert byte_limited["reason"] == "byte_budget"
    monkeypatch.setenv("BALLPARK_CLEANUP_MAX_SECONDS", "0")
    time_limited = maintain_publication_store(publication, now=observed)
    assert time_limited["state"] == "budget"
    assert time_limited["reason"] == "time_budget"


def test_precommit_failure_is_durable_and_runtime_reconciles_postcommit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BALLPARK_MIN_FREE_BYTES", "0")
    first = accept(tmp_path, 1)
    token = f"{9:032x}"
    stage(tmp_path, token, "2026-09-03")
    original = PublicationCatalog.commit

    def before_commit(*_args: object, **_kwargs: object) -> bool:
        raise OSError("before catalog commit")

    monkeypatch.setattr(PublicationCatalog, "commit", before_commit)
    with pytest.raises(OSError, match="before catalog"):
        _accept_stage(context(tmp_path, token))
    monkeypatch.setattr(PublicationCatalog, "commit", original)
    catalog = PublicationCatalog(tmp_path / "publication")
    assert catalog.current_manifest()["token"] == first
    database = sqlite3.connect(catalog.path)
    try:
        assert database.execute(
            "SELECT state FROM pending_candidates WHERE token=?", (token,)
        ).fetchone() == ("failed",)
    finally:
        database.close()

    calls: list[str] = []

    def handler(ctx: JobContext) -> None:
        registered_stage(tmp_path, ctx, "2026-09-04")

    def interrupted_acceptor(ctx: JobContext) -> None:
        _accept_stage(ctx)
        calls.append(ctx.token)
        raise OSError("after catalog commit")

    worker = RuntimeWorker(
        [JobSpec("publish", 60, 30)],
        {"publish": handler},
        state_dir=tmp_path / "runtime-state",
        cache_dir=tmp_path / "runtime-cache",
        publication_dir=tmp_path / "publication",
        acceptors={"publish": interrupted_acceptor},
        acceptance_reconcilers={"publish": _catalog_acceptance_reconciled},
    )
    result = worker.run_once()
    assert result["jobs"] == [{"job": "publish", "state": "succeeded"}]
    assert calls
    assert PublicationCatalog(tmp_path / "publication").accepted(calls[0]) is not None
