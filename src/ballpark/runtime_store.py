"""SQLite authority and lifecycle coordination for runtime publications."""

from __future__ import annotations

# ruff: noqa: E501
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

_SCHEMA_VERSION = 3
_CANDIDATE_STATES = {"preparing", "accepting", "accepted", "failed", "pending"}


def _stamp(value: datetime | None = None) -> str:
    observed = value or datetime.now(UTC)
    if observed.tzinfo is None:
        raise RuntimeError("catalog clock must include an offset")
    return observed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _instant(value: str) -> datetime:
    """Parse catalog timestamps exactly, including legacy whole-second values."""
    try:
        observed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise RuntimeError("catalog timestamp is invalid") from exc
    if observed.tzinfo is None:
        raise RuntimeError("catalog timestamp must include an offset")
    return observed.astimezone(UTC)


def _instant_key(value: str | None) -> str | None:
    """SQLite-indexable UTC key; invalid stored evidence is never treated as expired."""
    if not isinstance(value, str):
        return None
    try:
        observed = _instant(value)
    except RuntimeError:
        return None
    return (
        f"{observed.year:04d}{observed.month:02d}{observed.day:02d}"
        f"{observed.hour:02d}{observed.minute:02d}{observed.second:02d}{observed.microsecond:06d}"
    )


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or path.as_posix() != value
    ):
        raise RuntimeError("catalog-owned path is invalid")
    return value


class PublicationCatalog:
    """The catalog is the only authority that makes a release accepted.

    Candidates are retained as an audit trail. Their pending object references
    are many-to-many because a later candidate can reuse bytes first created by
    an interrupted candidate.
    """

    def __init__(self, publication: Path) -> None:
        self.publication = publication
        publication.mkdir(parents=True, exist_ok=True)
        self.path = publication / "catalog.sqlite3"
        self._initialize()

    def _connect(self, *, timeout: float = 30) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        db.create_function("catalog_instant_key", 1, _instant_key, deterministic=True)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _connection(self, *, timeout: float = 30):
        db = self._connect(timeout=timeout)
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _tables(db: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    @staticmethod
    def _create_tables(db: sqlite3.Connection) -> None:
        statements = """
            CREATE TABLE IF NOT EXISTS accepted_releases (
                token TEXT PRIMARY KEY,
                manifest TEXT NOT NULL,
                accepted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publication_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                current_token TEXT,
                rollback_token TEXT
            );
            CREATE TABLE IF NOT EXISTS protected_objects (
                digest TEXT PRIMARY KEY,
                token TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_candidates (
                token TEXT PRIMARY KEY,
                stage_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                error TEXT,
                state TEXT NOT NULL DEFAULT 'preparing',
                deadline_at TEXT NOT NULL,
                manifest TEXT
            );
            CREATE TABLE IF NOT EXISTS pending_objects (
                digest TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_object_refs (
                token TEXT NOT NULL REFERENCES pending_candidates(token),
                digest TEXT NOT NULL REFERENCES pending_objects(digest) ON DELETE CASCADE,
                PRIMARY KEY(token,digest)
            );
            CREATE TABLE IF NOT EXISTS pending_object_temps (
                token TEXT NOT NULL REFERENCES pending_candidates(token),
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(token,relative_path)
            );
            CREATE TABLE IF NOT EXISTS pending_stage_paths (
                token TEXT NOT NULL REFERENCES pending_candidates(token),
                relative_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(token,relative_path)
            );
            CREATE TABLE IF NOT EXISTS catalog_errors (
                id INTEGER PRIMARY KEY,
                token TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS pending_objects_created
                ON pending_objects(created_at,digest);
            CREATE INDEX IF NOT EXISTS pending_object_refs_digest
                ON pending_object_refs(digest,token);
            CREATE INDEX IF NOT EXISTS pending_candidates_active
                ON pending_candidates(state,deadline_at,token);
            CREATE INDEX IF NOT EXISTS pending_candidates_active_instant
                ON pending_candidates(state,catalog_instant_key(deadline_at),token);
            CREATE INDEX IF NOT EXISTS pending_stage_paths_created
                ON pending_stage_paths(created_at,token,relative_path);
            CREATE INDEX IF NOT EXISTS pending_object_temps_created
                ON pending_object_temps(created_at,token,relative_path);
            """
        for statement in statements.split(";"):
            if statement.strip():
                db.execute(statement)

    @classmethod
    def _validate_legacy_schema(cls, db: sqlite3.Connection, tables: set[str]) -> None:
        required = {
            "accepted_releases",
            "publication_state",
            "protected_objects",
            "pending_candidates",
            "pending_objects",
            "catalog_errors",
        }
        if tables != required:
            raise RuntimeError("publication catalog schema is unknown; refusing to mutate it")
        expected = {
            "accepted_releases": {"token", "manifest", "accepted_at"},
            "publication_state": {"id", "current_token", "rollback_token"},
            "protected_objects": {"digest", "token"},
            "pending_objects": {"digest", "token", "relative_path", "created_at"},
            "catalog_errors": {"id", "token", "message", "created_at"},
        }
        for table, columns in expected.items():
            if cls._table_columns(db, table) != columns:
                raise RuntimeError("publication catalog schema is unknown; refusing to mutate it")
        candidate_columns = cls._table_columns(db, "pending_candidates")
        if not {"token", "stage_path", "created_at", "error"}.issubset(candidate_columns):
            raise RuntimeError("publication catalog schema is unknown; refusing to mutate it")
        if candidate_columns - {
            "token",
            "stage_path",
            "created_at",
            "error",
            "state",
            "deadline_at",
            "manifest",
        }:
            raise RuntimeError("publication catalog schema is unknown; refusing to mutate it")
        states = (
            {
                str(row[0])
                for row in db.execute(
                    "SELECT DISTINCT state FROM pending_candidates WHERE state IS NOT NULL"
                )
            }
            if "state" in candidate_columns
            else set()
        )
        if not states.issubset(_CANDIDATE_STATES):
            raise RuntimeError("publication catalog state is unknown; refusing to mutate it")

    @classmethod
    def _validate_current_schema(cls, db: sqlite3.Connection, tables: set[str]) -> None:
        expected = {
            "accepted_releases": {"token", "manifest", "accepted_at"},
            "publication_state": {"id", "current_token", "rollback_token"},
            "protected_objects": {"digest", "token"},
            "pending_candidates": {
                "token",
                "stage_path",
                "created_at",
                "error",
                "state",
                "deadline_at",
                "manifest",
            },
            "pending_objects": {"digest", "token", "relative_path", "created_at"},
            "pending_object_refs": {"token", "digest"},
            "pending_object_temps": {"token", "relative_path", "created_at"},
            "pending_stage_paths": {"token", "relative_path", "created_at"},
            "catalog_errors": {"id", "token", "message", "created_at"},
        }
        if tables != set(expected) or any(
            cls._table_columns(db, table) != columns for table, columns in expected.items()
        ):
            raise RuntimeError("publication catalog schema is incompatible")

    def _initialize(self) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                tables = self._tables(db)
                if version == _SCHEMA_VERSION:
                    self._validate_current_schema(db, tables)
                    # Index additions are preserving, and keep maintenance
                    # bounded when opening a catalog made by this schema.
                    self._create_tables(db)
                elif version == 0 and not tables:
                    self._create_tables(db)
                    db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                elif version == 0:
                    self._validate_legacy_schema(db, tables)
                    candidate_columns = self._table_columns(db, "pending_candidates")
                    if "state" not in candidate_columns:
                        db.execute(
                            "ALTER TABLE pending_candidates ADD COLUMN state TEXT NOT NULL DEFAULT 'preparing'"
                        )
                    if "deadline_at" not in candidate_columns:
                        db.execute("ALTER TABLE pending_candidates ADD COLUMN deadline_at TEXT")
                    if "manifest" not in candidate_columns:
                        db.execute("ALTER TABLE pending_candidates ADD COLUMN manifest TEXT")
                    db.execute(
                        "UPDATE pending_candidates SET state='preparing' "
                        "WHERE state='pending' OR state IS NULL"
                    )
                    # Old candidates did not record liveness. Preserve them, but
                    # never let pre-upgrade metadata keep cleanup blocked forever.
                    db.execute(
                        "UPDATE pending_candidates SET deadline_at=created_at WHERE deadline_at IS NULL"
                    )
                    self._create_tables(db)
                    db.execute(
                        "INSERT OR IGNORE INTO pending_object_refs(token,digest) "
                        "SELECT token,digest FROM pending_objects"
                    )
                    db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
                else:
                    raise RuntimeError("publication catalog schema version is incompatible")
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    @classmethod
    def read_current(cls, publication: Path) -> dict[str, Any] | None:
        path = publication / "catalog.sqlite3"
        if not path.is_file():
            return None
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = db.execute(
                "SELECT manifest FROM accepted_releases JOIN publication_state ON token=current_token "
                "WHERE id=1"
            ).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            db.close()

    def current_manifest(self) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT manifest FROM accepted_releases JOIN publication_state ON token=current_token "
                "WHERE id=1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def accepted(self, token: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT manifest FROM accepted_releases WHERE token=?", (token,)).fetchone()
        return json.loads(row[0]) if row else None

    def register_candidate(
        self, token: str, stage_path: Path, created_at: str, deadline_at: str
    ) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT stage_path FROM pending_candidates WHERE token=?", (token,)
                ).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO pending_candidates"
                        "(token,stage_path,created_at,deadline_at,state) VALUES(?,?,?,?, 'preparing')",
                        (token, str(stage_path), created_at, deadline_at),
                    )
                elif row[0] != str(stage_path):
                    raise RuntimeError("publication token is already bound to a different stage")
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def register_stage_paths(self, token: str, paths: set[str], created_at: str) -> None:
        rows = [(token, _relative_path(path), created_at) for path in sorted(paths)]
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM pending_candidates WHERE token=?", (token,)).fetchone() is None:
                    raise RuntimeError("publication candidate is not registered")
                db.executemany(
                    "INSERT OR IGNORE INTO pending_stage_paths(token,relative_path,created_at) VALUES(?,?,?)",
                    rows,
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def begin_accept(
        self,
        token: str,
        now: str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> bool:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                observed = now or _stamp(clock() if clock is not None else None)
                row = db.execute(
                    "SELECT state,deadline_at FROM pending_candidates WHERE token=?", (token,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("publication candidate is not registered")
                state, deadline_at = str(row[0]), str(row[1])
                if state == "accepted":
                    db.execute("COMMIT")
                    return False
                if state == "failed":
                    raise RuntimeError("publication candidate previously failed")
                if _instant(deadline_at) <= _instant(observed):
                    db.execute(
                        "UPDATE pending_candidates SET state='failed',error=? WHERE token=?",
                        ("publication candidate deadline elapsed", token),
                    )
                    db.execute("COMMIT")
                    raise TimeoutError("publication candidate deadline elapsed")
                db.execute(
                    "UPDATE pending_candidates SET state='accepting',error=NULL WHERE token=?", (token,)
                )
                db.execute("COMMIT")
                return True
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def set_candidate_manifest(self, token: str, manifest: dict[str, Any]) -> None:
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT state,manifest FROM pending_candidates WHERE token=?", (token,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("publication candidate is not registered")
                if row[1] is not None and row[1] != encoded:
                    raise RuntimeError("publication token conflicts with candidate content")
                if row[0] not in {"accepting", "accepted"}:
                    raise RuntimeError("publication candidate is not accepting")
                db.execute("UPDATE pending_candidates SET manifest=? WHERE token=?", (encoded, token))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def register_object(self, digest: str, token: str, relative_path: str, created_at: str) -> None:
        relative_path = _relative_path(relative_path)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM pending_candidates WHERE token=?", (token,)).fetchone() is None:
                    raise RuntimeError("publication candidate is not registered")
                row = db.execute(
                    "SELECT relative_path FROM pending_objects WHERE digest=?", (digest,)
                ).fetchone()
                if row is None:
                    db.execute(
                        "INSERT INTO pending_objects(digest,token,relative_path,created_at) VALUES(?,?,?,?)",
                        (digest, token, relative_path, created_at),
                    )
                elif row[0] != relative_path:
                    raise RuntimeError("immutable object digest has conflicting path")
                db.execute(
                    "INSERT OR IGNORE INTO pending_object_refs(token,digest) VALUES(?,?)", (token, digest)
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def register_object_temp(self, token: str, relative_path: str, created_at: str) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if db.execute("SELECT 1 FROM pending_candidates WHERE token=?", (token,)).fetchone() is None:
                    raise RuntimeError("publication candidate is not registered")
                db.execute(
                    "INSERT OR IGNORE INTO pending_object_temps(token,relative_path,created_at) VALUES(?,?,?)",
                    (token, _relative_path(relative_path), created_at),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def complete_object_temp(self, token: str, relative_path: str) -> None:
        with self._connection() as db:
            db.execute(
                "DELETE FROM pending_object_temps WHERE token=? AND relative_path=?",
                (token, _relative_path(relative_path)),
            )

    def commit(
        self,
        token: str,
        manifest: dict[str, Any],
        digests: set[str],
        accepted_at: str | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> bool:
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # The deadline fence is authoritative only after this transaction owns
                # the catalog write lock. A callable preserves deterministic worker
                # clocks without turning a pre-lock sample into a stale authority.
                observed = _stamp(clock() if clock is not None else None)
                accepted = accepted_at or observed
                old = db.execute(
                    "SELECT manifest FROM accepted_releases WHERE token=?", (token,)
                ).fetchone()
                candidate = db.execute(
                    "SELECT state,deadline_at,manifest FROM pending_candidates WHERE token=?", (token,)
                ).fetchone()
                if old:
                    if old[0] != encoded:
                        raise RuntimeError("publication token conflicts with accepted content")
                    if candidate is not None:
                        db.execute(
                            "UPDATE pending_candidates SET state='accepted',manifest=?,error=NULL WHERE token=?",
                            (encoded, token),
                        )
                        db.execute("DELETE FROM pending_object_temps WHERE token=?", (token,))
                    self._clear_protected_pending(db)
                    db.execute("COMMIT")
                    return False
                if candidate is not None:
                    state, deadline_at, recorded = candidate
                    if state != "accepting":
                        raise RuntimeError("publication candidate is not accepting")
                    if recorded is not None and recorded != encoded:
                        raise RuntimeError("publication token conflicts with candidate content")
                    # Recheck after BEGIN IMMEDIATE: waiting for maintenance must
                    # not permit an expired candidate to resurrect.
                    if _instant(str(deadline_at)) <= _instant(observed):
                        db.execute(
                            "UPDATE pending_candidates SET state='failed',error=? WHERE token=?",
                            ("publication candidate deadline elapsed before commit", token),
                        )
                        db.execute("COMMIT")
                        raise TimeoutError("publication candidate deadline elapsed")
                prior = db.execute("SELECT current_token FROM publication_state WHERE id=1").fetchone()
                db.execute(
                    "INSERT INTO accepted_releases(token,manifest,accepted_at) VALUES(?,?,?)",
                    (token, encoded, accepted),
                )
                db.executemany(
                    "INSERT OR IGNORE INTO protected_objects(digest,token) VALUES(?,?)",
                    ((digest, token) for digest in digests),
                )
                db.execute(
                    "INSERT INTO publication_state(id,current_token,rollback_token) VALUES(1,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET rollback_token=current_token,"
                    "current_token=excluded.current_token",
                    (token, prior[0] if prior else None),
                )
                if candidate is not None:
                    db.execute(
                        "UPDATE pending_candidates SET state='accepted',manifest=?,error=NULL WHERE token=?",
                        (encoded, token),
                    )
                    db.execute("DELETE FROM pending_object_temps WHERE token=?", (token,))
                self._clear_protected_pending(db)
                db.execute("COMMIT")
                return True
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    @staticmethod
    def _clear_protected_pending(db: sqlite3.Connection) -> None:
        db.execute(
            "DELETE FROM pending_object_refs WHERE digest IN (SELECT digest FROM protected_objects)"
        )
        db.execute(
            "DELETE FROM pending_objects WHERE NOT EXISTS "
            "(SELECT 1 FROM pending_object_refs WHERE pending_object_refs.digest=pending_objects.digest)"
        )

    def candidate_matches_accepted(self, token: str) -> bool:
        with self._connection() as db:
            row = db.execute(
                "SELECT c.manifest,a.manifest FROM pending_candidates c "
                "JOIN accepted_releases a ON a.token=c.token WHERE c.token=?",
                (token,),
            ).fetchone()
        return row is not None and row[0] is not None and row[0] == row[1]

    def fail_candidate(self, token: str, message: str, created_at: str) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "UPDATE pending_candidates SET state='failed',error=? "
                    "WHERE token=? AND state IN ('preparing','accepting')",
                    (message, token),
                )
                db.execute(
                    "INSERT INTO catalog_errors(token,message,created_at) VALUES(?,?,?)",
                    (token, message, created_at),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def record_error(self, token: str, message: str, created_at: str) -> None:
        self.fail_candidate(token, message, created_at)

    def maintenance(self, cutoff: str, limit: int) -> list[tuple[str, str, str]]:
        """Compatibility read-only view of the next bounded object batch."""
        with self._connection() as db:
            return db.execute(
                "SELECT p.digest,COALESCE((SELECT token FROM pending_object_refs r "
                "WHERE r.digest=p.digest ORDER BY token LIMIT 1),p.token),p.relative_path "
                "FROM pending_objects p WHERE p.created_at <= ? ORDER BY p.created_at,p.digest LIMIT ?",
                (cutoff, limit),
            ).fetchall()

    def maintain(
        self,
        *,
        now: str,
        cutoff: str,
        entry_limit: int,
        byte_limit: int,
        time_limit_seconds: float,
        remove: Callable[[str, str, str, int], tuple[bool, int, str | None]],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> dict[str, Any]:
        """Clean catalog-confirmed temporary paths while holding the fence.

        ``remove`` must never follow an unowned link and returns ``(removed,
        bytes_removed, diagnostic)``. It runs inside the same IMMEDIATE catalog
        transaction as final protection and candidate-state checks.
        """
        outcome: dict[str, Any] = {
            "state": "completed",
            "inspected": 0,
            "deleted": 0,
            "metadata_cleared": 0,
            "bytes": 0,
            "reason": None,
        }
        if entry_limit <= 0 or byte_limit < 0 or time_limit_seconds < 0:
            outcome.update(state="budget", reason="invalid_budget")
            return outcome
        now_key = _instant_key(now)
        cutoff_key = _instant_key(cutoff)
        if now_key is None or cutoff_key is None:
            raise RuntimeError("catalog maintenance timestamp is invalid")
        start = monotonic()
        try:
            with self._connection(timeout=0.1) as db:
                try:
                    db.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    outcome.update(state="busy", reason="catalog_busy")
                    return outcome
                try:
                    invalid_active = db.execute(
                        "SELECT 1 FROM pending_candidates WHERE state IN ('preparing','accepting') "
                        "AND catalog_instant_key(deadline_at) IS NULL LIMIT 1"
                    ).fetchone()
                    active = invalid_active or db.execute(
                        "SELECT 1 FROM pending_candidates WHERE state IN ('preparing','accepting') "
                        "AND catalog_instant_key(deadline_at) > ? LIMIT 1",
                        (now_key,),
                    ).fetchone()
                    if active is not None:
                        db.execute("COMMIT")
                        outcome.update(state="skipped_active", reason="active_candidate")
                        return outcome

                    object_rows = db.execute(
                        "SELECT digest,relative_path FROM pending_objects WHERE created_at <= ? "
                        "AND NOT EXISTS (SELECT 1 FROM pending_object_refs r "
                        "JOIN pending_candidates c ON c.token=r.token "
                        "WHERE r.digest=pending_objects.digest AND "
                        "(catalog_instant_key(c.deadline_at) IS NULL "
                        "OR catalog_instant_key(c.deadline_at) > ?)) "
                        "ORDER BY created_at,digest LIMIT ?",
                        (cutoff, cutoff_key, entry_limit),
                    ).fetchall()
                    for digest, relative_path in object_rows:
                        if monotonic() - start >= time_limit_seconds:
                            outcome.update(state="budget", reason="time_budget")
                            break
                        outcome["inspected"] += 1
                        protected = db.execute(
                            "SELECT 1 FROM protected_objects WHERE digest=?", (digest,)
                        ).fetchone()
                        if protected is not None:
                            db.execute("DELETE FROM pending_object_refs WHERE digest=?", (digest,))
                            db.execute("DELETE FROM pending_objects WHERE digest=?", (digest,))
                            outcome["metadata_cleared"] += 1
                            continue
                        removed, size, diagnostic = remove(
                            "object", str(digest), str(relative_path), byte_limit - outcome["bytes"]
                        )
                        if diagnostic == "byte_budget":
                            outcome.update(state="budget", reason="byte_budget")
                            break
                        if removed:
                            db.execute("DELETE FROM pending_object_refs WHERE digest=?", (digest,))
                            db.execute("DELETE FROM pending_objects WHERE digest=?", (digest,))
                            outcome["deleted"] += 1
                            outcome["bytes"] += size
                        elif diagnostic:
                            outcome["reason"] = diagnostic
                        if monotonic() - start >= time_limit_seconds:
                            outcome.update(state="budget", reason="time_budget")
                            break

                    remaining = entry_limit - int(outcome["inspected"])
                    emptied_stage_tokens: set[str] = set()
                    if remaining > 0 and outcome["state"] == "completed":
                        temp_rows = db.execute(
                            "SELECT t.token,t.relative_path FROM pending_object_temps t "
                            "JOIN pending_candidates c ON c.token=t.token "
                            "WHERE t.created_at <= ? AND catalog_instant_key(c.deadline_at) <= ? "
                            "ORDER BY t.created_at,t.token,t.relative_path LIMIT ?",
                            (cutoff, cutoff_key, remaining),
                        ).fetchall()
                        for token, relative_path in temp_rows:
                            if monotonic() - start >= time_limit_seconds:
                                outcome.update(state="budget", reason="time_budget")
                                break
                            outcome["inspected"] += 1
                            removed, size, diagnostic = remove(
                                "temp", str(token), str(relative_path), byte_limit - outcome["bytes"]
                            )
                            if diagnostic == "byte_budget":
                                outcome.update(state="budget", reason="byte_budget")
                                break
                            if removed:
                                db.execute(
                                    "DELETE FROM pending_object_temps WHERE token=? AND relative_path=?",
                                    (token, relative_path),
                                )
                                outcome["deleted"] += 1
                                outcome["bytes"] += size
                            elif diagnostic:
                                outcome["reason"] = diagnostic

                    remaining = entry_limit - int(outcome["inspected"])
                    if remaining > 0 and outcome["state"] == "completed":
                        stage_rows = db.execute(
                            "SELECT s.token,s.relative_path FROM pending_stage_paths s "
                            "JOIN pending_candidates c ON c.token=s.token "
                            "WHERE s.created_at <= ? AND catalog_instant_key(c.deadline_at) <= ? "
                            "ORDER BY s.created_at,s.token,s.relative_path LIMIT ?",
                            (cutoff, cutoff_key, remaining),
                        ).fetchall()
                        for token, relative_path in stage_rows:
                            if monotonic() - start >= time_limit_seconds:
                                outcome.update(state="budget", reason="time_budget")
                                break
                            outcome["inspected"] += 1
                            removed, size, diagnostic = remove(
                                "stage", str(token), str(relative_path), byte_limit - outcome["bytes"]
                            )
                            if diagnostic == "byte_budget":
                                outcome.update(state="budget", reason="byte_budget")
                                break
                            if removed:
                                db.execute(
                                    "DELETE FROM pending_stage_paths WHERE token=? AND relative_path=?",
                                    (token, relative_path),
                                )
                                if db.execute(
                                    "SELECT 1 FROM pending_stage_paths WHERE token=? LIMIT 1",
                                    (token,),
                                ).fetchone() is None:
                                    emptied_stage_tokens.add(str(token))
                                outcome["deleted"] += 1
                                outcome["bytes"] += size
                            elif diagnostic:
                                outcome["reason"] = diagnostic
                    if outcome["state"] == "completed":
                        for token in sorted(emptied_stage_tokens):
                            _removed, _size, diagnostic = remove(
                                "stage_dir", token, "", byte_limit - outcome["bytes"]
                            )
                            if diagnostic:
                                outcome["reason"] = diagnostic
                    db.execute("COMMIT")
                    return outcome
                except Exception:
                    if db.in_transaction:
                        db.execute("ROLLBACK")
                    raise
        except sqlite3.OperationalError:
            outcome.update(state="busy", reason="catalog_busy")
            return outcome

    def remove_pending_object(self, digest: str) -> None:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM pending_object_refs WHERE digest=?", (digest,))
                db.execute("DELETE FROM pending_objects WHERE digest=?", (digest,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def delete_pending_if_unprotected(self, digest: str, remove: Callable[[], None]) -> bool:
        """Backward-compatible one-row cleanup with the same acceptance fence."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT 1 FROM pending_objects WHERE digest=? AND digest NOT IN "
                    "(SELECT digest FROM protected_objects)",
                    (digest,),
                ).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    return False
                remove()
                db.execute("DELETE FROM pending_object_refs WHERE digest=?", (digest,))
                db.execute("DELETE FROM pending_objects WHERE digest=?", (digest,))
                db.execute("COMMIT")
                return True
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def pending_candidates(self, cutoff: str, limit: int) -> list[tuple[str, str]]:
        with self._connection() as db:
            return db.execute(
                "SELECT token,stage_path FROM pending_candidates "
                "WHERE catalog_instant_key(deadline_at) <= catalog_instant_key(?) "
                "ORDER BY created_at,token LIMIT ?",
                (cutoff, limit),
            ).fetchall()

    def remove_pending_candidate(self, token: str) -> None:
        """Retain lifecycle evidence while making a legacy caller's candidate terminal."""
        with self._connection() as db:
            db.execute(
                "UPDATE pending_candidates SET state='failed' "
                "WHERE token=? AND state IN ('preparing','accepting')",
                (token,),
            )
