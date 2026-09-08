from __future__ import annotations

import base64
import hashlib
import json
import socket
import sqlite3
import threading
import time
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

import ballpark.runtime_monitor as runtime_monitor_module
from ballpark.cli import _build_parser, main
from ballpark.espn_odds import unavailable_market
from ballpark.runtime import RuntimeLedger
from ballpark.runtime_backup import (
    backup_runtime_mount,
    restore_runtime_mount,
    verify_runtime_backup,
)
from ballpark.runtime_monitor import (
    HttpResponse,
    _default_get,
    expected_new_york_date,
    monitor_from_environment,
    monitor_runtime,
    monitor_with_watchdog,
)
from ballpark.runtime_store import PublicationCatalog
from tests.support import valid_payload_document

_IMAGE = "ballpark@sha256:" + "f" * 64
_NOW = datetime(2026, 8, 26, 16, 5, tzinfo=UTC)


def _blocked_dns_monitor_runner(**parameters: object) -> dict[str, object]:
    original = socket.getaddrinfo

    def blocked(*args: object, **kwargs: object) -> object:
        time.sleep(30)
        return original(*args, **kwargs)

    socket.getaddrinfo = blocked  # type: ignore[assignment]
    try:
        return monitor_from_environment(**parameters)  # type: ignore[arg-type]
    finally:
        socket.getaddrinfo = original  # type: ignore[assignment]


def test_runtime_monitor_cli_accepts_no_ping_url_argument() -> None:
    args = _build_parser().parse_args(
        ["runtime-monitor", "--url", "https://runtime.example/", "--expected-date", "2026-08-26"]
    )
    assert args.command == "runtime-monitor"
    assert args.max_publication_age_seconds == 600
    assert args.max_maintenance_age_seconds == 600
    assert args.max_run_seconds == 60
    with pytest.raises(SystemExit):
        _build_parser().parse_args(
            [
                "runtime-monitor",
                "--url",
                "https://runtime.example/",
                "--ping-url",
                "https://monitor.example/secret",
            ]
        )


def test_runtime_monitor_cli_dispatches_to_the_process_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def ready(*_args: object, **kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {"state": "ready"}

    monkeypatch.setattr(runtime_monitor_module, "monitor_with_watchdog", ready)
    assert (
        main(
            [
                "runtime-monitor",
                "--url",
                "https://runtime.example/",
                "--expected-date",
                "2026-08-26",
                "--timeout-seconds",
                "5",
                "--max-run-seconds",
                "5",
            ]
        )
        == 0
    )
    assert observed["timeout_seconds"] == 5
    assert observed["max_run_seconds"] == 5


def test_process_watchdog_terminates_a_monitor_stuck_in_dns() -> None:
    started = time.monotonic()
    result = monitor_with_watchdog(
        "https://runtime.example/",
        expected_date=date(2026, 8, 26),
        timeout_seconds=1,
        max_run_seconds=5,
        _runner=_blocked_dns_monitor_runner,
    )
    elapsed = time.monotonic() - started

    assert result == {
        "state": "unhealthy",
        "problems": ["monitor run exceeded its total deadline"],
        "monitor": {"state": "not_configured"},
    }
    assert elapsed < 6.5


def _routes(
    payload: dict[str, object],
    *,
    readiness_status: int = 200,
    readiness_state: str = "ready",
    maintenance_state: str = "succeeded",
    maintenance_at: str = "2026-08-26T16:00:00Z",
) -> tuple[dict[str, HttpResponse], list[str]]:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    release = json.dumps(
        {"date": payload["date"], "payload_sha256": hashlib.sha256(raw).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    calls: list[str] = []
    routes = {
        "/healthz": HttpResponse(200, b'{"state":"live"}'),
        "/readiness": HttpResponse(
            readiness_status, json.dumps({"state": readiness_state}).encode()
        ),
        "/data/release.json": HttpResponse(200, release),
        "/data/data.json": HttpResponse(200, raw),
        "/source-health": HttpResponse(
            200,
            json.dumps(
                {
                    "state": "available",
                    "date": payload["date"],
                    "sportsbook": {
                        "latest_attempted_at": "2026-08-26T16:00:00Z",
                        "latest_status": "observed",
                        "last_good_captured_at": "2026-08-26T16:00:00Z",
                    },
                    "maintenance": {
                        "observed_at": maintenance_at,
                        "state": maintenance_state,
                    },
                }
            ).encode(),
        ),
        "/monitor-secret": HttpResponse(200, b""),
    }

    def get(url: str, _timeout: float) -> HttpResponse:
        path = urlparse(url).path
        calls.append(path)
        return routes[path]

    return get, calls  # type: ignore[return-value]


def test_external_monitor_checks_binding_and_pings_only_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_payload_document()
    get, calls = _routes(payload)
    monkeypatch.setenv("BALLPARK_MONITOR_PING_URL", "https://monitor.example/monitor-secret")

    result = monitor_from_environment(
        "https://runtime.example/",
        expected_date=date(2026, 8, 26),
        timeout_seconds=10,
        get=get,
        now=_NOW,
    )

    assert result["state"] == "ready"
    assert result["monitor"] == {"state": "pinged"}
    assert calls == [
        "/healthz",
        "/readiness",
        "/data/release.json",
        "/data/data.json",
        "/source-health",
        "/monitor-secret",
    ]
    assert "monitor-secret" not in json.dumps(result)


def test_external_monitor_distinguishes_missing_ping_and_tomorrow_staleness() -> None:
    payload = valid_payload_document()
    get, calls = _routes(payload)

    not_configured = monitor_runtime(
        "https://runtime.example/", expected_date=date(2026, 8, 26), get=get, now=_NOW
    )
    assert not_configured["state"] == "monitor_not_configured"
    assert not_configured["monitor"] == {"state": "not_configured"}
    assert calls == [
        "/healthz",
        "/readiness",
        "/data/release.json",
        "/data/data.json",
        "/source-health",
    ]

    get, calls = _routes(payload)
    stale = monitor_runtime(
        "https://runtime.example/",
        expected_date=date(2026, 8, 27),
        ping_url="https://monitor.example/monitor-secret",
        get=get,
        now=_NOW,
    )
    assert stale["state"] == "unhealthy"
    assert "expected New York date" in stale["problems"][0]
    assert calls == ["/healthz", "/readiness", "/data/release.json", "/data/data.json"]


@pytest.mark.parametrize(
    ("generated_at", "problem"),
    [
        ("2026-08-26T15:54:59Z", "published payload is stale"),
        ("2026-08-26T16:10:01Z", "published payload timestamp is in the future"),
    ],
)
def test_external_monitor_rejects_stuck_or_future_same_day_publication(
    generated_at: str, problem: str
) -> None:
    payload = valid_payload_document()
    payload["generated_at"] = generated_at
    get, calls = _routes(payload)

    result = monitor_runtime(
        "https://runtime.example/",
        expected_date=date(2026, 8, 26),
        ping_url="https://monitor.example/monitor-secret",
        get=get,
        now=_NOW,
    )

    assert result == {
        "state": "unhealthy",
        "problems": [problem],
        "monitor": {"state": "not_configured"},
    }
    assert calls == ["/healthz", "/readiness", "/data/release.json", "/data/data.json"]


def test_external_monitor_reports_provider_outage_without_pinging_or_restarting() -> None:
    payload = valid_payload_document()
    payload["games"][0]["odds"] = unavailable_market(  # type: ignore[index]
        810001,
        date(2026, 8, 26),
        "ESPN scoreboard update failed",
        observed_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    payload["health"]["odds"] = {  # type: ignore[index]
        "state": "unavailable",
        "source": "ESPN public scoreboard / DraftKings",
        "current_games": 0,
        "observed_unknown_age_games": 0,
        "stale_games": 0,
        "unavailable_games": 1,
        "optional": False,
        "acquisition_status": "transport_error",
        "acquisition_error": "timeout",
    }
    get, calls = _routes(payload)

    result = monitor_runtime(
        "https://runtime.example/",
        expected_date=date(2026, 8, 26),
        ping_url="https://monitor.example/monitor-secret",
        get=get,
        now=_NOW,
    )

    assert result["state"] == "source_degraded"
    assert result["monitor"] == {"state": "not_configured"}
    assert result["problems"] == ["sportsbook source attempt failed"]
    assert calls == [
        "/healthz",
        "/readiness",
        "/data/release.json",
        "/data/data.json",
        "/source-health",
    ]


@pytest.mark.parametrize(
    ("maintenance_state", "maintenance_at", "problem"),
    [
        ("failed", "2026-08-26T16:00:00Z", "publication maintenance failed"),
        ("succeeded", "2026-08-26T15:54:59Z", "publication maintenance is stale"),
    ],
)
def test_external_monitor_surfaces_maintenance_failure_or_staleness(
    maintenance_state: str, maintenance_at: str, problem: str
) -> None:
    get, calls = _routes(
        valid_payload_document(),
        maintenance_state=maintenance_state,
        maintenance_at=maintenance_at,
    )

    result = monitor_runtime(
        "https://runtime.example/",
        expected_date=date(2026, 8, 26),
        ping_url="https://monitor.example/monitor-secret",
        get=get,
        now=_NOW,
    )

    assert result["state"] == "source_degraded"
    assert result["problems"] == [problem]
    assert calls == [
        "/healthz",
        "/readiness",
        "/data/release.json",
        "/data/data.json",
        "/source-health",
    ]


def test_external_monitor_detects_worker_unready_and_dst_business_date() -> None:
    payload = valid_payload_document()
    get, calls = _routes(payload, readiness_status=503, readiness_state="not_ready")

    result = monitor_runtime(
        "https://runtime.example/", expected_date=date(2026, 8, 26), get=get, now=_NOW
    )
    assert result["state"] == "unhealthy"
    assert calls == ["/healthz", "/readiness"]
    assert expected_new_york_date(datetime(2026, 3, 8, 4, 30, tzinfo=UTC)) == date(2026, 3, 7)
    assert expected_new_york_date(datetime(2026, 3, 8, 7, 30, tzinfo=UTC)) == date(2026, 3, 8)
    assert expected_new_york_date(datetime(2026, 11, 1, 5, 30, tzinfo=UTC)) == date(2026, 11, 1)


def test_external_monitor_stops_at_its_total_run_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = valid_payload_document()
    get, calls = _routes(payload)
    elapsed = 0.0

    def monotonic() -> float:
        return elapsed

    def late_get(url: str, timeout_seconds: float) -> HttpResponse:
        nonlocal elapsed
        response = get(url, timeout_seconds)
        elapsed = 61.0
        return response

    monkeypatch.setattr(runtime_monitor_module.time, "monotonic", monotonic)
    result = monitor_runtime(
        "https://runtime.example/",
        expected_date=date(2026, 8, 26),
        get=late_get,
        now=_NOW,
        max_run_seconds=60,
    )

    assert result == {
        "state": "unhealthy",
        "problems": ["monitor run exceeded its total deadline"],
        "monitor": {"state": "not_configured"},
    }
    assert calls == ["/healthz"]


def test_default_monitor_get_decodes_chunked_http_response() -> None:
    class ChunkedHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in (b'{"state":', b'"live"}'):
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\nX-Probe: complete\r\n\r\n")
            self.wfile.flush()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), ChunkedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = _default_get(f"http://127.0.0.1:{server.server_port}/", 2.0)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert response.status == 200
    assert response.body == b'{"state":"live"}'
    assert json.loads(response.body) == {"state": "live"}


def test_default_monitor_get_enforces_total_deadline_during_a_slow_drip() -> None:
    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = b"slow-response"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return
                time.sleep(0.3)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="total deadline"):
            _default_get(f"http://127.0.0.1:{server.server_port}/", 1.0)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert time.monotonic() - started < 1.8


def _mount(tmp_path: Path) -> tuple[Path, bytes]:
    mount = tmp_path / "mount"
    for name in (Path("state"), Path("cache") / "sources", Path("publication") / "objects"):
        (mount / name).mkdir(parents=True, exist_ok=True)
    raw = b'{"events":["private capture"]}'
    receipt = {
        "attempted_at": "2026-11-01T05:30:00Z",
        "raw_response_b64": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "last_good_captured_at": "2026-11-01T05:10:00Z",
    }
    (mount / "cache" / "sources" / "sportsbook.json").write_text(
        json.dumps(receipt, sort_keys=True), encoding="utf-8"
    )
    (mount / "state" / "operations.json").write_text(
        json.dumps({"maintenance_history": [{"observed_at": "2026-11-01T05:30:00Z"}]}),
        encoding="utf-8",
    )
    ledger = RuntimeLedger(mount / "state")
    ledger.update(
        lambda state: state.update(
            {
                "heartbeat_at": "2026-11-01T05:30:00Z",
                "jobs": {"sportsbook": {"last_success_at": "2026-11-01T05:10:00Z"}},
            }
        )
    )
    catalog = PublicationCatalog(mount / "publication")
    token = "a" * 32
    catalog.commit(
        token,
        {
            "schema_version": 3,
            "token": token,
            "data_sha256": "b" * 64,
            "release_sha256": "c" * 64,
            "archive_index_sha256": "d" * 64,
            "input_receipts": {"sportsbook": "e" * 64},
        },
        {"b" * 64, "c" * 64, "d" * 64, "e" * 64},
        "2026-11-01T05:30:00Z",
    )
    return mount, raw


def test_stopped_backup_restore_preserves_private_raw_clocks_and_catalog_history(
    tmp_path: Path,
) -> None:
    mount, raw = _mount(tmp_path)
    backup = tmp_path / "backup"

    saved = backup_runtime_mount(mount=mount, destination=backup, image_ref=_IMAGE)
    assert saved["state"] == "backed_up"
    assert saved["catalog"]["current_token"] == "a" * 32
    assert verify_runtime_backup(backup, image_ref=_IMAGE)["state"] == "verified"

    restored = tmp_path / "restored"
    restored.mkdir()
    result = restore_runtime_mount(backup=backup, destination=restored, image_ref=_IMAGE)
    assert result["state"] == "restored"
    receipt = json.loads((restored / "cache" / "sources" / "sportsbook.json").read_text())
    assert base64.b64decode(receipt["raw_response_b64"]) == raw
    assert receipt["attempted_at"] == "2026-11-01T05:30:00Z"
    assert receipt["last_good_captured_at"] == "2026-11-01T05:10:00Z"
    assert (
        RuntimeLedger(restored / "state").read()["jobs"]["sportsbook"]["last_success_at"]
        == "2026-11-01T05:10:00Z"
    )
    assert PublicationCatalog(restored / "publication").current_manifest()["token"] == "a" * 32
    assert not (restored / "BALLPARK_BACKUP_MANIFEST.json").exists()

    follow_up = tmp_path / "follow-up-backup"
    assert backup_runtime_mount(
        mount=restored, destination=follow_up, image_ref=_IMAGE
    )["state"] == "backed_up"
    assert verify_runtime_backup(follow_up, image_ref=_IMAGE)["state"] == "verified"


def test_backup_rejects_live_lease_and_corrupt_backup_before_restore(tmp_path: Path) -> None:
    mount, _raw = _mount(tmp_path)
    ledger = RuntimeLedger(mount / "state")
    ledger.update(
        lambda state: state.update(
            {"writer": {"until_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat()}}
        )
    )
    with pytest.raises(RuntimeError, match="lease is still active"):
        backup_runtime_mount(mount=mount, destination=tmp_path / "blocked", image_ref=_IMAGE)

    ledger.update(lambda state: state.pop("writer", None))
    catalog_lock = sqlite3.connect(mount / "publication" / "catalog.sqlite3")
    catalog_lock.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="database is busy"):
            backup_runtime_mount(
                mount=mount,
                destination=tmp_path / "catalog-locked",
                image_ref=_IMAGE,
            )
    finally:
        catalog_lock.rollback()
        catalog_lock.close()

    backup = tmp_path / "backup"
    backup_runtime_mount(mount=mount, destination=backup, image_ref=_IMAGE)
    (backup / "cache" / "sources" / "sportsbook.json").write_text("altered", encoding="utf-8")
    destination = tmp_path / "never-restored"
    with pytest.raises(RuntimeError, match="hash differs"):
        restore_runtime_mount(backup=backup, destination=destination, image_ref=_IMAGE)
    assert not destination.exists()
