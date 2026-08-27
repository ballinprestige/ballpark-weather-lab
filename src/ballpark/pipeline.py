from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ballpark.artifacts import ArtifactReceipt, verify_artifacts
from ballpark.contract import validate_payload
from ballpark.errors import DataContractError
from ballpark.http import HttpClient
from ballpark.lineups import fetch_lineup
from ballpark.model import ParkFactorModel
from ballpark.paths import ProjectPaths
from ballpark.physics import PhysicsEngine, trajectory_theater
from ballpark.publication import publish_payload
from ballpark.schedule import fetch_schedule
from ballpark.venues import VENUES
from ballpark.weather import fetch_game_weather, neutral_weather


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_fixture(path: Path, target_date: date) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError(f"fixture is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise DataContractError("fixture must be an object")
    if value.get("date") != target_date.isoformat():
        raise DataContractError("fixture date does not match requested date")
    if not isinstance(value.get("schedule"), list):
        raise DataContractError("fixture schedule must be a list")
    if not isinstance(value.get("weather_by_game", {}), dict):
        raise DataContractError("fixture weather_by_game must be an object")
    if not isinstance(value.get("lineups_by_game", {}), dict):
        raise DataContractError("fixture lineups_by_game must be an object")
    return value


def _health_state(verified: int, total: int) -> str:
    if total == 0:
        return "not_applicable"
    if verified == total:
        return "available"
    if verified == 0:
        return "unavailable"
    return "partial"


def _public_lineup(lineup: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": lineup["state"],
        "reason": lineup.get("reason"),
        "observed_at": lineup.get("observed_at"),
        "home_count": len(lineup.get("home_batter_ids") or []),
        "away_count": len(lineup.get("away_batter_ids") or []),
    }


class DailyPipeline:
    def __init__(self, paths: ProjectPaths, *, client: HttpClient | None = None):
        self.paths = paths
        self.client = client or HttpClient()

    def build(
        self,
        target_date: date,
        *,
        fixture_path: Path | None = None,
        generated_at: str | None = None,
    ) -> dict[str, Any]:
        generated_at = generated_at or utc_now()
        network_deadline = time.monotonic() + 180.0
        receipt = verify_artifacts(self.paths)
        fixture = load_fixture(fixture_path, target_date) if fixture_path else None
        schedule = fixture["schedule"] if fixture else fetch_schedule(target_date, self.client)
        game_ids = [int(game["game_pk"]) for game in schedule]
        if len(game_ids) != len(set(game_ids)):
            raise DataContractError("schedule contains duplicate game IDs")

        if not schedule:
            payload = self._no_slate(
                target_date,
                generated_at,
                receipt,
                source="fixture" if fixture else "MLB Stats API",
            )
            validate_payload(payload, self.paths.schemas / "slate.schema.json")
            return payload

        model = ParkFactorModel(self.paths.models, self.paths.data)
        physics: PhysicsEngine | None = None
        games: list[dict[str, Any]] = []
        weather_verified = 0
        lineups_confirmed = 0
        lineup_unavailable = 0
        weather_fixture = fixture.get("weather_by_game", {}) if fixture else {}
        lineup_fixture = fixture.get("lineups_by_game", {}) if fixture else {}

        for scheduled in schedule:
            home_team = str(scheduled.get("home_team") or "")
            if home_team not in VENUES:
                raise DataContractError(f"unsupported home venue team: {home_team!r}")
            venue = VENUES[home_team]
            game_pk = int(scheduled["game_pk"])
            if fixture:
                weather = weather_fixture.get(str(game_pk))
                if not isinstance(weather, dict):
                    weather = neutral_weather(game_pk, venue, "fixture omits game-hour weather")
                lineup = lineup_fixture.get(str(game_pk))
                if not isinstance(lineup, dict):
                    lineup = {
                        "state": "not_yet_available",
                        "reason": "fixture omits official batting orders",
                        "observed_at": generated_at,
                        "home_batter_ids": [],
                        "away_batter_ids": [],
                    }
            else:
                if time.monotonic() >= network_deadline:
                    weather = neutral_weather(
                        game_pk, venue, "daily network deadline elapsed before weather fetch"
                    )
                    lineup = {
                        "state": "unavailable",
                        "reason": "daily network deadline elapsed before lineup fetch",
                        "observed_at": None,
                        "home_batter_ids": [],
                        "away_batter_ids": [],
                    }
                else:
                    weather = fetch_game_weather(scheduled, venue, self.client)
                    lineup = (
                        fetch_lineup(game_pk, self.client)
                        if time.monotonic() < network_deadline
                        else {
                            "state": "unavailable",
                            "reason": "daily network deadline elapsed before lineup fetch",
                            "observed_at": None,
                            "home_batter_ids": [],
                            "away_batter_ids": [],
                        }
                    )

            if weather.get("state") == "verified":
                weather_verified += 1
            if lineup.get("state") == "confirmed":
                lineups_confirmed += 1
            if lineup.get("state") == "unavailable":
                lineup_unavailable += 1

            factors = model.predict(target_date=target_date, venue=venue, weather=weather)
            if lineup.get("state") == "confirmed" and weather.get("state") == "verified":
                try:
                    if receipt.approach_c_state != "verified":
                        raise RuntimeError("optional lineup/trajectory artifacts did not verify")
                    physics = physics or PhysicsEngine.load(self.paths.data)
                    approach_c = physics.approach_c(lineup, venue=venue, weather=weather)
                except Exception as exc:
                    approach_c = {
                        "state": "not_available",
                        "reason": f"lineup physics could not be evaluated: {exc}",
                        "used_in_headline": False,
                        "method": "neutral-park double ratio",
                    }
            else:
                approach_c = {
                    "state": "not_available",
                    "reason": (
                        weather.get("reason")
                        if weather.get("state") != "verified"
                        else lineup.get("reason") or "official batting orders are not confirmed"
                    ),
                    "used_in_headline": False,
                    "method": "neutral-park double ratio",
                }

            games.append(
                {
                    "game_pk": game_pk,
                    "game_date": target_date.isoformat(),
                    "game_time": scheduled.get("game_time") or "",
                    "game_status": scheduled.get("game_status") or "Unknown",
                    "game_number": int(scheduled.get("game_number") or 1),
                    "doubleheader": str(scheduled.get("doubleheader") or "N"),
                    "home_team": home_team,
                    "away_team": str(scheduled.get("away_team") or ""),
                    "venue": str(scheduled.get("venue") or venue.name),
                    "home_pitcher": scheduled.get("home_pitcher"),
                    "away_pitcher": scheduled.get("away_pitcher"),
                    "weather": weather,
                    "factors": factors,
                    "lineup": _public_lineup(lineup),
                    "approach_c": approach_c,
                    "trajectory": trajectory_theater(venue, weather),
                }
            )

        weather_state = _health_state(weather_verified, len(games))
        if lineups_confirmed == len(games):
            lineup_state = "available"
        elif lineups_confirmed > 0:
            lineup_state = "partial"
        elif lineup_unavailable == len(games):
            lineup_state = "unavailable"
        else:
            lineup_state = "not_yet_available"
        payload = {
            "schema_version": 1,
            "product": "ballpark-weather-lab",
            "date": target_date.isoformat(),
            "generated_at": generated_at,
            "status": "ready" if weather_verified == len(games) else "degraded",
            "no_slate_reason": None,
            "model": self._model_receipt(receipt),
            "health": {
                "schedule": {
                    "state": "available",
                    "source": "fixture" if fixture else "MLB Stats API",
                    "game_count": len(games),
                },
                "weather": {
                    "state": weather_state,
                    "source": "fixture" if fixture else "Open-Meteo",
                    "verified_games": weather_verified,
                    "held_games": len(games) - weather_verified,
                },
                "lineups": {
                    "state": lineup_state,
                    "source": "fixture" if fixture else "MLB Stats API game feeds",
                    "confirmed_games": lineups_confirmed,
                    "optional": True,
                },
                "artifacts": receipt.as_dict(),
            },
            "games": games,
        }
        validate_payload(payload, self.paths.schemas / "slate.schema.json")
        return payload

    def build_and_publish(
        self,
        target_date: date,
        output_root: Path,
        *,
        fixture_path: Path | None = None,
        generated_at: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self.build(
            target_date,
            fixture_path=fixture_path,
            generated_at=generated_at,
        )
        return payload, publish_payload(output_root, payload)

    @staticmethod
    def _model_receipt(receipt: ArtifactReceipt) -> dict[str, Any]:
        return {
            "name": "Approach B weather-adjusted park factors",
            "artifact_version": "2026-04-10",
            "evidence_games": receipt.evidence_games,
            "split": {"train": 17_075, "validation_2024": 2_302, "test_2025": 2_231},
            "held_out_rmse": {"runs": 0.5102, "home_runs": 0.7173},
            "statement": (
                "Held-out RMSE describes this training experiment; it is not a claim of "
                "downstream outcome or decision performance."
            ),
            "approach_c": {
                "state": "experimental_optional",
                "used_in_headline": False,
                "batter_profiles": receipt.batter_profiles,
                "trajectory_entries": receipt.trajectory_entries,
                "stadium_geometries": receipt.stadium_geometries,
            },
        }

    def _no_slate(
        self,
        target_date: date,
        generated_at: str,
        receipt: ArtifactReceipt,
        *,
        source: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "product": "ballpark-weather-lab",
            "date": target_date.isoformat(),
            "generated_at": generated_at,
            "status": "no_slate",
            "no_slate_reason": (
                "MLB reports no games scheduled for this date."
                if source == "MLB Stats API"
                else "The verification fixture contains no scheduled games for this date."
            ),
            "model": self._model_receipt(receipt),
            "health": {
                "schedule": {"state": "available", "source": source, "game_count": 0},
                "weather": {
                    "state": "not_applicable",
                    "source": "fixture" if source == "fixture" else "Open-Meteo",
                    "verified_games": 0,
                    "held_games": 0,
                },
                "lineups": {
                    "state": "not_applicable",
                    "source": "fixture" if source == "fixture" else "MLB Stats API game feeds",
                    "confirmed_games": 0,
                    "optional": True,
                },
                "artifacts": receipt.as_dict(),
            },
            "games": [],
        }
