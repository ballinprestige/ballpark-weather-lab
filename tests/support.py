from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from ballpark.venues import Venue

TARGET_DATE = date(2026, 8, 26)
GENERATED_AT = "2026-08-26T16:00:00Z"


class FakeParkFactorModel:
    """Fast deterministic replacement for model-loading in pipeline integration tests."""

    initializations = 0

    def __init__(self, _models_dir: object, _data_dir: object):
        type(self).initializations += 1

    def predict(
        self,
        *,
        target_date: date,
        venue: Venue,
        weather: dict[str, Any],
    ) -> dict[str, Any]:
        del target_date
        seasonal_runs = round(venue.seasonal_pf_runs, 4)
        seasonal_hr = 1.04
        if weather.get("state") != "verified":
            return {
                "state": "held",
                "reason": "game-hour weather is not verified; seasonal baselines remain visible",
                "seasonal_pf_runs": seasonal_runs,
                "seasonal_pf_hr": seasonal_hr,
                "weather_multiplier_runs": 1.0,
                "weather_multiplier_hr": 1.0,
                "game_pf_runs": seasonal_runs,
                "game_pf_hr": seasonal_hr,
                "weather_delta_runs": 0.0,
                "weather_delta_hr": 0.0,
                "hr_baseline_as_of": "2026-08-26",
            }
        runs_multiplier = 1.025
        hr_multiplier = 1.05
        return {
            "state": "modeled",
            "reason": None,
            "seasonal_pf_runs": seasonal_runs,
            "seasonal_pf_hr": seasonal_hr,
            "weather_multiplier_runs": runs_multiplier,
            "weather_multiplier_hr": hr_multiplier,
            "game_pf_runs": round(seasonal_runs * runs_multiplier, 4),
            "game_pf_hr": round(seasonal_hr * hr_multiplier, 4),
            "weather_delta_runs": round(seasonal_runs * runs_multiplier - seasonal_runs, 4),
            "weather_delta_hr": round(seasonal_hr * hr_multiplier - seasonal_hr, 4),
            "hr_baseline_as_of": "2026-08-26",
        }


class FakePhysicsEngine:
    loads = 0

    @classmethod
    def load(cls, _data_dir: object) -> FakePhysicsEngine:
        cls.loads += 1
        return cls()

    def approach_c(
        self,
        lineup: dict[str, Any],
        *,
        venue: Venue,
        weather: dict[str, Any],
    ) -> dict[str, Any]:
        del lineup, venue, weather
        return {
            "state": "experimental",
            "reason": "Optional lineup geometry is shown separately from the headline factor.",
            "used_in_headline": False,
            "method": "neutral-park double ratio",
            "home_hr_index": 1.031,
            "away_hr_index": 0.987,
            "home_minus_away": 0.044,
            "home_profile_coverage": 9,
            "away_profile_coverage": 9,
        }


def fast_trajectory(_venue: Venue, weather: dict[str, Any]) -> dict[str, Any]:
    if weather.get("state") != "verified":
        return {
            "state": "held",
            "reason": "trajectory comparison is hidden without verified game-hour weather",
            "integration": "bounded Euler approximation",
            "arcs": [],
        }
    return {
        "state": "available",
        "reason": None,
        "integration": "bounded Euler approximation",
        "arcs": [],
    }


def valid_weather(game_pk: int = 810001) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "state": "verified",
        "source": "fixture",
        "basis": "forecast",
        "reason": None,
        "valid_at": "2026-08-26T23:00:00Z",
        "fetched_at": "2026-08-26T16:00:00Z",
        "temperature_f": 78.0,
        "humidity_pct": 54.0,
        "wind_speed_mph": 8.0,
        "wind_direction_deg": 225.0,
        "wind_carry_mph": 4.2,
        "wind_cross_mph": -6.8,
        "air_density_index": 95.7,
        "pressure_hpa": 1008.4,
        "dome_active": False,
        "roof_state": "open-air",
    }


def valid_payload_document(*, slate_date: str = "2026-08-26") -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "product": "ballpark-weather-lab",
        "date": slate_date,
        "generated_at": GENERATED_AT,
        "status": "ready",
        "no_slate_reason": None,
        "model": {
            "name": "Approach B weather-adjusted park factors",
            "artifact_version": "2026-04-10",
            "evidence_games": 21_608,
            "split": {"train": 17_075, "validation_2024": 2_302, "test_2025": 2_231},
            "held_out_rmse": {"runs": 0.5102, "home_runs": 0.7173},
            "statement": "Held-out error describes the documented model experiment.",
            "approach_c": {
                "state": "experimental_optional",
                "used_in_headline": False,
                "batter_profiles": 839,
                "trajectory_entries": 3_018_625,
                "stadium_geometries": 30,
            },
        },
        "health": {
            "schedule": {"state": "available", "source": "fixture", "game_count": 1},
            "weather": {
                "state": "available",
                "source": "fixture",
                "verified_games": 1,
                "held_games": 0,
            },
            "lineups": {
                "state": "not_yet_available",
                "source": "fixture",
                "confirmed_games": 0,
                "optional": True,
            },
            "artifacts": {
                "state": "verified",
                "approach_c_state": "verified",
                "optional_errors": [],
                "manifest_sha256": "a" * 64,
                "files_checked": 9,
                "evidence_games": 21_608,
                "batter_profiles": 839,
                "trajectory_entries": 3_018_625,
                "stadium_geometries": 30,
            },
        },
        "games": [
            {
                "game_pk": 810001,
                "game_date": slate_date,
                "game_time": "2026-08-26T23:10:00Z",
                "game_status": "Scheduled",
                "game_number": 1,
                "doubleheader": "N",
                "home_team": "BOS",
                "away_team": "NYY",
                "venue": "Fenway Park",
                "home_pitcher": None,
                "away_pitcher": None,
                "weather": valid_weather(),
                "factors": {
                    "state": "modeled",
                    "reason": None,
                    "seasonal_pf_runs": 1.03,
                    "seasonal_pf_hr": 1.04,
                    "weather_multiplier_runs": 1.025,
                    "weather_multiplier_hr": 1.05,
                    "game_pf_runs": 1.0558,
                    "game_pf_hr": 1.092,
                    "weather_delta_runs": 0.0258,
                    "weather_delta_hr": 0.052,
                    "hr_baseline_as_of": "2026-08-26",
                },
                "lineup": {
                    "state": "not_yet_available",
                    "reason": "official batting orders have not been posted",
                    "observed_at": GENERATED_AT,
                    "home_count": 0,
                    "away_count": 0,
                },
                "approach_c": {
                    "state": "not_available",
                    "reason": "official batting orders have not been posted",
                    "used_in_headline": False,
                    "method": "neutral-park double ratio",
                },
                "trajectory": {
                    "state": "available",
                    "reason": None,
                    "integration": "bounded Euler approximation",
                    "arcs": [],
                },
            }
        ],
    }
    return deepcopy(payload)
