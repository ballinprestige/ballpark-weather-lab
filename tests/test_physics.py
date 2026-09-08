from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter

import pandas as pd
import pytest

from ballpark.paths import ProjectPaths
from ballpark.physics import (
    APPROACH_C_METHOD,
    MAX_EVENT_EVALUATIONS,
    PhysicsEngine,
    air_density_ratio,
    simulate_wall_event,
)
from ballpark.venues import VENUES


def _engine(project_paths: ProjectPaths, profiles: pd.DataFrame | None = None) -> PhysicsEngine:
    data_dir = project_paths.data
    return PhysicsEngine(
        profiles if profiles is not None else pd.read_parquet(data_dir / "batter_profiles.parquet"),
        pd.read_parquet(data_dir / "park_geometry.parquet"),
        None,  # Wall events retain their own Euler evidence, not lookup display values.
    )


def _one_bin_profile(
    batter_id: int, *, ev: float, launch: float, spray_index: int
) -> dict[str, float]:
    profile: dict[str, float] = {"batter_id": batter_id}
    profile.update({f"ev_p{percentile}": ev for percentile in (5, 25, 50, 75, 95)})
    profile.update({f"la_p{percentile}": launch for percentile in (5, 25, 50, 75, 95)})
    profile.update({f"spray_bin_{index}": float(index == spray_index) for index in range(9)})
    return profile


@pytest.mark.parametrize(
    ("ev", "launch", "spray", "temperature", "altitude", "wind", "terminal", "crossing", "rate"),
    [
        (88, 36, -40, 67, 30, 0, "wall_contact", 35.888507, 0.0),
        (100, 28, -40, 67, 30, 0, "cleared_wall", 55.406917, 1.0),
        (104, 12, 40, 77, 30, 8, "ground_before_wall", None, 0.0),
        (120, 10, 40, 77, 30, 8, "cleared_wall", 6.931813, 1.0),
    ],
)
def test_wall_events_follow_the_unrounded_first_euler_event(
    project_paths: ProjectPaths,
    ev: int,
    launch: int,
    spray: int,
    temperature: int,
    altitude: int,
    wind: int,
    terminal: str,
    crossing: float | None,
    rate: float,
) -> None:
    engine = _engine(
        project_paths,
        pd.DataFrame([_one_bin_profile(1, ev=ev, launch=launch, spray_index=(spray + 40) // 10)]),
    )
    event = engine.wall_event(
        team="BOS",
        ev_mph=ev,
        launch_angle_deg=launch,
        spray_angle_deg=spray,
        temp_f=temperature,
        altitude_ft=altitude,
        wind_carry_mph=wind,
    )
    assert event is not None
    assert event.terminal == terminal
    if crossing is None:
        assert event.crossing_height_ft is None
    else:
        assert event.crossing_height_ft == pytest.approx(crossing, abs=0.000001)
    assert (
        engine.wall_clearance_rate(
            engine.profile_index[1],
            team="BOS",
            temp_f=temperature,
            altitude_ft=altitude,
            wind_carry_mph=wind,
        )
        == rate
    )


def test_wall_and_ground_order_equality_foul_and_incomplete_contract(
    project_paths: ProjectPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine(project_paths)
    density = air_density_ratio(77, 30)

    # These wall planes occur in the same final Euler step; the first segment event wins.
    wall_before_ground = simulate_wall_event(
        104, 12, 40, density, 8, wall_distance_ft=297.0, wall_height_ft=0.0
    )
    ground_before_wall = simulate_wall_event(
        104, 12, 40, density, 8, wall_distance_ft=298.0, wall_height_ft=0.0
    )
    assert wall_before_ground.terminal == "cleared_wall"
    assert ground_before_wall.terminal == "ground_before_wall"
    assert wall_before_ground.elapsed_seconds == ground_before_wall.elapsed_seconds

    clear = engine.wall_event(
        team="BOS",
        ev_mph=100,
        launch_angle_deg=28,
        spray_angle_deg=-40,
        temp_f=67,
        altitude_ft=30,
        wind_carry_mph=0,
    )
    assert clear is not None and clear.crossing_height_ft is not None
    equal_top = simulate_wall_event(
        100,
        28,
        -40,
        air_density_ratio(67, 30),
        0,
        wall_distance_ft=318.7,
        wall_height_ft=clear.crossing_height_ft,
    )
    assert equal_top.terminal == "wall_contact"

    called = False

    def fail_if_called(_team: str, _spray: float) -> tuple[float, float]:
        nonlocal called
        called = True
        raise AssertionError("foul input must not consult a fair-territory wall")

    monkeypatch.setattr(engine, "wall", fail_if_called)
    assert (
        engine.wall_event(
            team="BOS",
            ev_mph=100,
            launch_angle_deg=28,
            spray_angle_deg=60,
            temp_f=67,
            altitude_ft=30,
            wind_carry_mph=0,
        ).terminal
        == "foul"
    )
    assert not called
    assert (
        simulate_wall_event(
            100,
            28,
            -60,
            1.0,
            0,
            wall_distance_ft=300,
            wall_height_ft=8,
        ).terminal
        == "foul"
    )
    assert (
        simulate_wall_event(
            100,
            28,
            45,
            1.0,
            0,
            wall_distance_ft=300,
            wall_height_ft=8,
        ).terminal
        != "foul"
    )
    assert (
        simulate_wall_event(
            100,
            28,
            0,
            1.0,
            0,
            wall_distance_ft=300,
            wall_height_ft=8,
            max_time=0,
        ).terminal
        == "incomplete"
    )


def test_wall_event_cache_uses_quantized_conditions_and_wall_geometry(
    project_paths: ProjectPaths,
) -> None:
    engine = _engine(project_paths)
    base = {
        "team": "BOS",
        "ev_mph": 100.1,
        "launch_angle_deg": 27.9,
        "spray_angle_deg": -39.9,
        "temp_f": 67.1,
        "altitude_ft": 30.1,
        "wind_carry_mph": 0.1,
    }
    assert engine.wall_event(**base) is not None
    assert engine.wall_event(**{**base, "ev_mph": 100.4, "temp_f": 66.9}) is not None
    assert engine.event_evidence["evaluated_event_keys"] == 1

    for changed in (
        {"temp_f": 77.0},
        {"altitude_ft": 800.0},
        {"wind_carry_mph": 4.0},
        {"team": "NYY"},
    ):
        assert engine.wall_event(**{**base, **changed}) is not None
    evidence = engine.event_evidence
    assert evidence["requested_event_queries"] == 6
    assert evidence["evaluated_event_keys"] == 5
    assert evidence["method"] == APPROACH_C_METHOD
    assert evidence["budget_exhausted"] is False


def test_event_budget_holds_only_optional_approach_c(project_paths: ProjectPaths) -> None:
    profile = _one_bin_profile(101, ev=100, launch=28, spray_index=0)
    engine = _engine(project_paths, pd.DataFrame([profile]))
    engine.event_limit = 0
    result = engine.approach_c(
        {"state": "confirmed", "home_batter_ids": [101], "away_batter_ids": [101]},
        venue=VENUES["BOS"],
        weather={"temperature_f": 67.0, "wind_carry_mph": 0.0},
    )
    assert result["state"] == "not_available"
    assert "budget was exhausted" in result["reason"]
    assert result["used_in_headline"] is False
    assert engine.event_evidence["budget_exhausted"] is True


def test_fifteen_confirmed_games_stay_within_event_ceiling_and_build_deadline(
    project_paths: ProjectPaths, fixture_root: Path
) -> None:
    profiles = pd.read_parquet(project_paths.data / "batter_profiles.parquet")
    engine = _engine(project_paths, profiles)
    schedule = json.loads((fixture_root / "official_schedule_2026-09-08.json").read_text())
    assert len(schedule) == 15
    spray_columns = [f"spray_bin_{index}" for index in range(9)]
    batter_ids = (
        profiles.loc[profiles[spray_columns].gt(0).sum(axis=1).eq(9), "batter_id"]
        .head(18 * len(schedule))
        .astype(int)
        .tolist()
    )
    assert len(batter_ids) == 18 * len(schedule)
    weather_conditions = ((47.0, -8.0), (57.0, -4.0), (67.0, 0.0), (77.0, 4.0), (87.0, 8.0))
    started = perf_counter()
    outcomes = []
    for index, game in enumerate(schedule):
        game_batter_ids = batter_ids[index * 18 : (index + 1) * 18]
        temperature, wind = weather_conditions[index % len(weather_conditions)]
        outcomes.append(
            engine.approach_c(
                {
                    "state": "confirmed",
                    "home_batter_ids": game_batter_ids[:9],
                    "away_batter_ids": game_batter_ids[9:],
                },
                venue=VENUES[game["home_team"]],
                weather={"temperature_f": temperature, "wind_carry_mph": wind},
            )
        )
    elapsed = perf_counter() - started
    evidence = engine.event_evidence
    assert all(outcome["state"] == "experimental" for outcome in outcomes)
    assert all(outcome["used_in_headline"] is False for outcome in outcomes)
    assert evidence["requested_event_queries"] == 15 * 8_100
    assert evidence["requested_event_queries"] <= MAX_EVENT_EVALUATIONS
    assert evidence["evaluated_event_keys"] > 40_000
    assert evidence["evaluated_event_keys"] <= evidence["requested_event_queries"]
    assert evidence["budget_exhausted"] is False
    # The daily build's existing 180-second deadline retains at least two minutes of CPU headroom.
    assert elapsed < 60.0
