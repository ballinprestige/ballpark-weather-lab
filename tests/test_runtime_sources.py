from __future__ import annotations

import hashlib
import json
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

import ballpark.pipeline as pipeline_module
import ballpark.runtime_app as runtime_app
from ballpark.artifacts import ArtifactReceipt
from ballpark.kalshi import unavailable_market as unavailable_exchange_market
from ballpark.paths import ProjectPaths
from ballpark.runtime import RuntimeLedger
from ballpark.runtime_app import _load_sportsbook_receipt, _read_object, run_live_worker
from ballpark.runtime_server import RuntimeHandler
from ballpark.runtime_store import PublicationCatalog
from tests.support import FakeParkFactorModel, FakePhysicsEngine, fast_trajectory, valid_weather

TARGET = datetime(2026, 8, 26, 16, 0, tzinfo=UTC)


def _schedule() -> list[dict[str, object]]:
    return [
        {
            "game_pk": 810001,
            "game_date": "2026-08-26",
            "game_time": "2026-08-26T23:10:00Z",
            "game_status": "Scheduled",
            "game_number": 1,
            "doubleheader": "N",
            "home_team": "BOS",
            "away_team": "NYY",
            "venue": "Fenway Park",
            "home_pitcher": None,
            "away_pitcher": None,
        }
    ]


def _lineup() -> dict[str, object]:
    return {
        "state": "confirmed",
        "reason": None,
        "observed_at": "2026-08-26T15:55:00Z",
        "home_batter_ids": list(range(101, 110)),
        "away_batter_ids": list(range(201, 210)),
    }


def _scoreboard() -> bytes:
    return json.dumps(
        {
            "events": [
                {
                    "id": "espn-810001",
                    "competitions": [
                        {
                            "startDate": "2026-08-26T23:10:00Z",
                            "format": {"regulation": {"periods": 9}},
                            "status": {"type": {"state": "pre", "completed": False}},
                            "competitors": [
                                {"homeAway": "away", "team": {"abbreviation": "NYY"}},
                                {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                            ],
                            "odds": [
                                {
                                    "provider": {"id": "100", "name": "DraftKings"},
                                    "total": {
                                        "displayName": "Total Runs",
                                        "over": {"close": {"line": "o8.5", "odds": "-110"}},
                                        "under": {"close": {"line": "u8.5", "odds": "-110"}},
                                    },
                                }
                            ],
                        }
                    ],
                }
            ]
        },
        separators=(",", ":"),
    ).encode()


def _scoreboard_for(schedule: list[dict[str, object]]) -> bytes:
    """Return one ESPN bulk document whose events exactly bind this official slate."""
    template = json.loads(_scoreboard())
    event = template["events"][0]
    events: list[dict[str, object]] = []
    for game in schedule:
        row = deepcopy(event)
        row["id"] = f"espn-{game['game_pk']}"
        competition = row["competitions"][0]
        competition["startDate"] = game["game_time"]
        competition["competitors"] = [
            {"homeAway": "away", "team": {"abbreviation": game["away_team"]}},
            {"homeAway": "home", "team": {"abbreviation": game["home_team"]}},
        ]
        events.append(row)
    return json.dumps({"events": events}, separators=(",", ":")).encode()


def _scheduled_game(
    slate_date: str,
    *,
    game_pk: int = 810001,
    game_time: str | None = None,
    game_status: str = "Scheduled",
    game_number: int = 1,
    doubleheader: str = "N",
) -> dict[str, object]:
    game = _schedule()[0]
    game.update(
        game_pk=game_pk,
        game_date=slate_date,
        game_time=game_time or f"{slate_date}T23:10:00Z",
        game_status=game_status,
        game_number=game_number,
        doubleheader=doubleheader,
    )
    return game


class _Client:
    def __init__(self, response: bytes | Exception) -> None:
        self.response, self.calls, self.closed = response, [], False

    def get_bytes(self, url: str, *, deadline_at: float | None = None) -> bytes:
        self.calls.append((url, deadline_at))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


class _Clock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


class _DeadlineClient(_Client):
    def __init__(self, response: bytes | Exception, clock: _Clock) -> None:
        super().__init__(response)
        self.clock = clock

    def get_bytes(self, url: str, *, deadline_at: float | None = None) -> bytes:
        value = super().get_bytes(url, deadline_at=deadline_at)
        self.clock.advance(31)
        return value


class _Exchange:
    def __init__(self, _client: object, _path: Path, _clock: object) -> None:
        pass

    def fetch(
        self,
        schedule: list[dict[str, object]],
        *,
        observed_at: datetime,
        deadline_at: float | None,
    ) -> dict[int, dict[str, object]]:
        assert deadline_at is not None
        return {
            int(game["game_pk"]): unavailable_exchange_market(
                game, observed_at, "no exact Kalshi event"
            )
            for game in schedule
        }


class _Server:
    def __init__(self, root: Path) -> None:
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            type(
                "SourceRuntimeHandler",
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

    def get_json(self, path: str) -> dict[str, object]:
        with urlopen(f"http://127.0.0.1:{self.server.server_port}{path}") as response:
            assert response.status == 200
            return json.loads(response.read())


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = ArtifactReceipt(
        state="verified",
        approach_c_state="verified",
        optional_errors=(),
        manifest_sha256="a" * 64,
        files_checked=9,
        evidence_games=21_608,
        batter_profiles=839,
        trajectory_entries=3_018_625,
        stadium_geometries=30,
    )
    monkeypatch.setattr(pipeline_module, "verify_artifacts", lambda _paths: receipt)
    monkeypatch.setattr(pipeline_module, "verify_exported_geometry", lambda _root: None)
    monkeypatch.setattr(pipeline_module, "ParkFactorModel", FakeParkFactorModel)
    monkeypatch.setattr(pipeline_module, "PhysicsEngine", FakePhysicsEngine)
    monkeypatch.setattr(pipeline_module, "trajectory_theater", fast_trajectory)


def _sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_app, "fetch_schedule", lambda *_args, **_kwargs: _schedule())
    monkeypatch.setattr(
        runtime_app, "fetch_game_weather", lambda *_args, **_kwargs: valid_weather()
    )
    monkeypatch.setattr(runtime_app, "fetch_lineup", lambda *_args, **_kwargs: _lineup())


def _sources_for(monkeypatch: pytest.MonkeyPatch, schedule: list[dict[str, object]]) -> None:
    monkeypatch.setattr(runtime_app, "fetch_schedule", lambda *_args, **_kwargs: schedule)
    monkeypatch.setattr(
        runtime_app,
        "fetch_game_weather",
        lambda game, *_args, **_kwargs: valid_weather(int(game["game_pk"])),
    )
    monkeypatch.setattr(runtime_app, "fetch_lineup", lambda *_args, **_kwargs: _lineup())


def _run(
    paths: ProjectPaths,
    root: Path,
    *,
    now: datetime,
    response: bytes | Exception,
) -> tuple[dict[str, object], _Client]:
    client = _Client(response)
    result = run_live_worker(
        paths,
        state_dir=root / "state",
        cache_dir=root / "cache",
        publication_dir=root / "publication",
        once=True,
        clock=lambda: now,
        client_factory=lambda: client,  # type: ignore[arg-type]
        exchange_provider_factory=lambda source, path, source_clock: _Exchange(
            source, path, source_clock
        ),  # type: ignore[arg-type]
    )
    return result, client


def test_live_worker_retains_private_espn_receipt_and_surfaces_outage(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    _stub_pipeline(monkeypatch)
    _sources(monkeypatch)
    first, client = _run(project_paths, tmp_path, now=TARGET, response=_scoreboard())
    assert client.closed and len(client.calls) == 1
    assert {row["job"]: row["state"] for row in first["jobs"]}["sportsbook"] == "succeeded"
    catalog = PublicationCatalog(tmp_path / "publication")
    manifest = catalog.current_manifest()
    assert isinstance(manifest, dict)
    raw_receipt = _read_object(
        tmp_path / "publication" / "objects",
        manifest["input_receipts"]["sportsbook"],
        32 * 1024 * 1024,
    )
    assert hashlib.sha256(_scoreboard()).hexdigest() in raw_receipt.decode("utf-8")
    with _Server(tmp_path) as server:
        healthy = server.get_json("/data/data.json")
    assert healthy["health"]["odds"]["source"] == "ESPN public scoreboard / DraftKings"
    assert healthy["games"][0]["odds"]["observed_at"] == "2026-08-26T16:00:00Z"

    failed, offline = _run(
        project_paths,
        tmp_path,
        now=TARGET + timedelta(minutes=5),
        response=OSError("offline"),
    )
    assert offline.closed and len(offline.calls) == 1
    states = {row["job"]: row["state"] for row in failed["jobs"]}
    assert states["sportsbook"] == "retry_scheduled"
    assert states["publish"] == "succeeded"
    with _Server(tmp_path) as server:
        retained = server.get_json("/data/data.json")
        release = server.get_json("/data/release.json")
    odds = retained["games"][0]["odds"]
    assert odds["line"] == 8.5
    assert odds["observed_at"] == "2026-08-26T16:00:00Z"
    assert odds["failure_reason"] == "ESPN scoreboard update failed: OSError"
    assert retained["health"]["odds"]["acquisition_status"] == "transport_error"
    assert retained["health"]["odds"]["acquisition_error"] == "OSError"
    assert (
        release["payload_sha256"]
        == hashlib.sha256(
            json.dumps(retained, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            + b"\n"
        ).hexdigest()
    )
    ledger = RuntimeLedger(tmp_path / "state").read()
    assert (
        "ESPN sportsbook acquisition failed: OSError" in ledger["jobs"]["sportsbook"]["last_error"]
    )
    operations = json.loads((tmp_path / "state" / "operations.json").read_text())
    assert operations["maintenance"]["state"] == "succeeded"


def test_restart_receipt_rejects_changed_start_and_preserves_healthy_empty_history(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    _stub_pipeline(monkeypatch)
    _sources(monkeypatch)
    _run(project_paths, tmp_path, now=TARGET, response=_scoreboard())
    receipt = _load_sportsbook_receipt(
        tmp_path / "cache", TARGET.date(), _schedule(), comparison_now=TARGET + timedelta(minutes=1)
    )
    assert receipt["latest"]["status"] == "observed"
    changed = _schedule()
    changed[0]["game_time"] = "2026-08-26T23:11:00Z"
    with pytest.raises(RuntimeError, match="sportsbook receipt"):
        _load_sportsbook_receipt(
            tmp_path / "cache", TARGET.date(), changed, comparison_now=TARGET + timedelta(minutes=1)
        )

    _run(project_paths, tmp_path, now=TARGET + timedelta(minutes=5), response=b'{"events":[]}')
    empty = _load_sportsbook_receipt(
        tmp_path / "cache",
        TARGET.date(),
        _schedule(),
        comparison_now=TARGET + timedelta(minutes=5),
    )
    assert empty["acquisition"]["status"] == "no_quote"
    assert empty["markets"]["810001"]["state"] == "unavailable"
    assert empty["last_good"]["markets"]["810001"]["state"] == "observed_unknown_age"


def test_maintenance_failure_is_durable_without_failing_the_live_publication_pass(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    _stub_pipeline(monkeypatch)
    _sources(monkeypatch)

    def broken_maintenance(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise OSError("maintenance volume unavailable")

    monkeypatch.setattr(runtime_app, "maintain_publication_store", broken_maintenance)
    result, _client = _run(project_paths, tmp_path, now=TARGET, response=_scoreboard())
    assert {row["job"]: row["state"] for row in result["jobs"]}["publish"] == "succeeded"
    assert result["maintenance"]["state"] == "failed"
    operations = json.loads((tmp_path / "state" / "operations.json").read_text())
    assert operations["maintenance"]["state"] == "failed"
    assert "maintenance volume unavailable" in operations["maintenance"]["error"]


def test_first_espn_failure_publishes_no_invented_quote_with_a_failed_source_lane(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    _stub_pipeline(monkeypatch)
    _sources(monkeypatch)
    result, _client = _run(project_paths, tmp_path, now=TARGET, response=OSError("offline"))
    states = {row["job"]: row["state"] for row in result["jobs"]}
    assert states["sportsbook"] == "retry_scheduled"
    assert states["publish"] == "succeeded"
    with _Server(tmp_path) as server:
        payload = server.get_json("/data/data.json")
    odds = payload["games"][0]["odds"]
    assert odds["state"] == "unavailable"
    assert odds["line"] is odds["over_price"] is odds["under_price"] is None
    assert odds["failure_reason"] == "ESPN scoreboard update failed: OSError"
    assert payload["health"]["odds"]["acquisition_status"] == "transport_error"


def test_derived_real_20260908_capture_publishes_fourteen_draftkings_pairs(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    """Exercise worker→receipt→acceptor→HTTP with the reviewed 15-game capture shape."""
    _stub_pipeline(monkeypatch)
    schedule = json.loads(
        (Path(__file__).parent / "fixtures" / "official_schedule_2026-09-08.json").read_text()
    )
    document = json.loads(
        (Path(__file__).parent / "fixtures" / "espn_draftkings_2026-09-08_derived.json").read_text()
    )
    assert (
        document["source_raw_sha256"]
        == "8ef74591579e168bd9f8a5af23e129cd7b8eee2f1f8bd7020df7937792686132"
    )
    assert len(schedule) == 15 and len(document["events"]) == 14
    _sources_for(monkeypatch, schedule)
    result, client = _run(
        project_paths,
        tmp_path,
        now=datetime(2026, 9, 8, 5, 10, tzinfo=UTC),
        response=json.dumps(document, separators=(",", ":")).encode(),
    )
    assert client.closed and len(client.calls) == 1
    assert {row["job"]: row["state"] for row in result["jobs"]}["publish"] == "succeeded"
    with _Server(tmp_path) as server:
        payload = server.get_json("/data/data.json")
        release = server.get_json("/data/release.json")
    observed = [
        game for game in payload["games"] if game["odds"]["state"] == "observed_unknown_age"
    ]
    no_quote = next(game for game in payload["games"] if game["game_pk"] == 823092)
    assert len(payload["games"]) == 15 and len(observed) == 14
    assert no_quote["odds"]["state"] == "unavailable"
    assert payload["health"]["odds"]["acquisition_status"] == "observed"
    assert payload["health"]["odds"]["observed_unknown_age_games"] == 14
    assert (
        release["payload_sha256"]
        == hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            + b"\n"
        ).hexdigest()
    )


@pytest.mark.parametrize(
    ("now", "slate_date"),
    [
        (datetime(2026, 11, 1, 5, 30, tzinfo=UTC), "2026-11-01"),
        (datetime(2027, 1, 1, 5, 30, tzinfo=UTC), "2027-01-01"),
    ],
    ids=["fall_dst", "new_year"],
)
def test_live_worker_uses_new_york_date_across_dst_and_year_boundary(
    monkeypatch: pytest.MonkeyPatch,
    project_paths: ProjectPaths,
    tmp_path: Path,
    now: datetime,
    slate_date: str,
) -> None:
    _stub_pipeline(monkeypatch)
    schedule = [_scheduled_game(slate_date)]
    _sources_for(monkeypatch, schedule)
    result, client = _run(project_paths, tmp_path, now=now, response=_scoreboard_for(schedule))
    assert {row["job"]: row["state"] for row in result["jobs"]}["publish"] == "succeeded"
    assert len(client.calls) == 1 and f"dates={slate_date.replace('-', '')}" in client.calls[0][0]
    catalog = PublicationCatalog(tmp_path / "publication")
    manifest = catalog.current_manifest()
    assert isinstance(manifest, dict)
    payload = json.loads(
        _read_object(
            tmp_path / "publication" / "objects", manifest["data_sha256"], 32 * 1024 * 1024
        )
    )
    assert payload["date"] == slate_date


def test_runtime_source_rejects_after_start_and_binds_each_doubleheader_game(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    _stub_pipeline(monkeypatch)
    after_start = [
        _scheduled_game(
            "2026-08-26",
            game_time="2026-08-26T15:59:00Z",
            game_status="In Progress",
        )
    ]
    _sources_for(monkeypatch, after_start)
    result, _client = _run(
        project_paths,
        tmp_path,
        now=TARGET,
        response=_scoreboard_for(after_start),
    )
    assert {row["job"]: row["state"] for row in result["jobs"]}["sportsbook"] == "succeeded"
    with _Server(tmp_path) as server:
        payload = server.get_json("/data/data.json")
    assert payload["games"][0]["odds"]["state"] == "unavailable"
    assert payload["health"]["odds"]["acquisition_status"] == "no_quote"

    doubleheader = [
        _scheduled_game(
            "2026-08-27",
            game_pk=810101,
            game_time="2026-08-27T18:10:00Z",
            game_number=1,
            doubleheader="Y",
        ),
        _scheduled_game(
            "2026-08-27",
            game_pk=810102,
            game_time="2026-08-27T22:10:00Z",
            game_number=2,
            doubleheader="Y",
        ),
    ]
    _sources_for(monkeypatch, doubleheader)
    result, client = _run(
        project_paths,
        tmp_path,
        now=datetime(2026, 8, 27, 15, tzinfo=UTC),
        response=_scoreboard_for(doubleheader),
    )
    assert len(client.calls) == 1
    assert {row["job"]: row["state"] for row in result["jobs"]}["publish"] == "succeeded", result
    with _Server(tmp_path) as server:
        payload = server.get_json("/data/data.json")
    assert {game["game_pk"] for game in payload["games"]} == {810101, 810102}
    assert all(game["odds"]["state"] == "observed_unknown_age" for game in payload["games"])


def test_deadline_exhaustion_keeps_prior_sportsbook_receipt_publishable(
    monkeypatch: pytest.MonkeyPatch, project_paths: ProjectPaths, tmp_path: Path
) -> None:
    _stub_pipeline(monkeypatch)
    _sources(monkeypatch)
    _run(project_paths, tmp_path, now=TARGET, response=_scoreboard())
    clock = _Clock(TARGET + timedelta(minutes=5))
    client = _DeadlineClient(_scoreboard(), clock)
    result = run_live_worker(
        project_paths,
        state_dir=tmp_path / "state",
        cache_dir=tmp_path / "cache",
        publication_dir=tmp_path / "publication",
        once=True,
        clock=clock,
        client_factory=lambda: client,  # type: ignore[arg-type]
        exchange_provider_factory=lambda source, path, source_clock: _Exchange(
            source, path, source_clock
        ),  # type: ignore[arg-type]
    )
    states = {row["job"]: row["state"] for row in result["jobs"]}
    assert client.closed and len(client.calls) == 1 and client.calls[0][1] is not None
    assert states["sportsbook"] == "retry_scheduled"
    assert states["publish"] == "succeeded"
    with _Server(tmp_path) as server:
        payload = server.get_json("/data/data.json")
    odds = payload["games"][0]["odds"]
    assert odds["observed_at"] == "2026-08-26T16:00:00Z"
    assert odds["failure_reason"] == "ESPN scoreboard update failed: source_job_deadline_elapsed"
    assert payload["health"]["odds"]["acquisition_status"] == "transport_error"
    assert payload["health"]["odds"]["acquisition_error"] == "source_job_deadline_elapsed"
    ledger = RuntimeLedger(tmp_path / "state").read()
    assert "deadline elapsed" in ledger["jobs"]["sportsbook"]["last_error"]
