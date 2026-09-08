"""SQLite authority for immutable runtime publications."""

from __future__ import annotations

# ruff: noqa: E501
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class PublicationCatalog:
    def __init__(self, publication: Path) -> None:
        self.publication = publication
        publication.mkdir(parents=True, exist_ok=True)
        self.path = publication / "catalog.sqlite3"
        with self._connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS accepted_releases (token TEXT PRIMARY KEY, manifest TEXT NOT NULL, accepted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publication_state (id INTEGER PRIMARY KEY CHECK(id=1), current_token TEXT, rollback_token TEXT);
            CREATE TABLE IF NOT EXISTS protected_objects (digest TEXT PRIMARY KEY, token TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pending_candidates (token TEXT PRIMARY KEY, stage_path TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS pending_objects (digest TEXT PRIMARY KEY, token TEXT NOT NULL, relative_path TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS catalog_errors (id INTEGER PRIMARY KEY, token TEXT, message TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS pending_objects_created ON pending_objects(created_at,digest);
            CREATE INDEX IF NOT EXISTS pending_candidates_created ON pending_candidates(created_at,token);
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    @classmethod
    def read_current(cls, publication: Path) -> dict[str, Any] | None:
        path = publication / "catalog.sqlite3"
        if not path.is_file():
            return None
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT manifest FROM accepted_releases JOIN publication_state ON token=current_token WHERE id=1"
            ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            db.close()

    def current_manifest(self) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT manifest FROM accepted_releases JOIN publication_state ON token=current_token WHERE id=1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def accepted(self, token: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT manifest FROM accepted_releases WHERE token=?", (token,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def register_candidate(self, token: str, stage_path: Path, created_at: str) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO pending_candidates(token,stage_path,created_at) VALUES(?,?,?)",
                (token, str(stage_path), created_at),
            )

    def register_object(self, digest: str, token: str, relative_path: str, created_at: str) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO pending_objects VALUES(?,?,?,?)",
                (digest, token, relative_path, created_at),
            )

    def commit(
        self, token: str, manifest: dict[str, Any], digests: set[str], accepted_at: str
    ) -> bool:
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT manifest FROM accepted_releases WHERE token=?", (token,)
            ).fetchone()
            if old:
                if old[0] != encoded:
                    db.execute("ROLLBACK")
                    raise RuntimeError("publication token conflicts with accepted content")
                db.execute("ROLLBACK")
                return False
            prior = db.execute("SELECT current_token FROM publication_state WHERE id=1").fetchone()
            db.execute("INSERT INTO accepted_releases VALUES(?,?,?)", (token, encoded, accepted_at))
            db.executemany(
                "INSERT OR IGNORE INTO protected_objects VALUES(?,?)", ((d, token) for d in digests)
            )
            db.execute(
                "INSERT INTO publication_state(id,current_token,rollback_token) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET rollback_token=current_token,current_token=excluded.current_token",
                (token, prior[0] if prior else None),
            )
            db.execute("DELETE FROM pending_objects WHERE token=?", (token,))
            db.execute("COMMIT")
            return True

    def maintenance(self, cutoff: str, limit: int) -> list[tuple[str, str, str]]:
        with self._connection() as db:
            return db.execute(
                "SELECT digest,token,relative_path FROM pending_objects WHERE created_at <= ? AND digest NOT IN (SELECT digest FROM protected_objects) ORDER BY created_at,digest LIMIT ?",
                (cutoff, limit),
            ).fetchall()

    def remove_pending_object(self, digest: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM pending_objects WHERE digest=?", (digest,))

    def pending_candidates(self, cutoff: str, limit: int) -> list[tuple[str, str]]:
        with self._connection() as db:
            return db.execute(
                "SELECT token,stage_path FROM pending_candidates WHERE created_at <= ? ORDER BY created_at,token LIMIT ?",
                (cutoff, limit),
            ).fetchall()

    def remove_pending_candidate(self, token: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM pending_candidates WHERE token=?", (token,))

    def record_error(self, token: str, message: str, created_at: str) -> None:
        with self._connection() as db:
            db.execute(
                "INSERT INTO catalog_errors(token,message,created_at) VALUES(?,?,?)",
                (token, message, created_at),
            )
