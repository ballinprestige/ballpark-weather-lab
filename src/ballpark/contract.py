from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ballpark.errors import DataContractError

RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=(TypeError, ValueError))
def _is_rfc3339_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if RFC3339_DATETIME.fullmatch(value) is None:
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None


def load_schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataContractError(f"payload schema is unreadable: {path}") from exc
    if not isinstance(schema, dict):
        raise DataContractError("payload schema is not an object")
    return schema


def validate_payload(payload: dict[str, Any], schema_path: Path) -> None:
    validator = Draft202012Validator(load_schema(schema_path), format_checker=FORMAT_CHECKER)
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise DataContractError(f"payload schema violation at {location}: {error.message}")
    games = payload.get("games") or []
    game_ids = [game.get("game_pk") for game in games if isinstance(game, dict)]
    if len(game_ids) != len(set(game_ids)):
        raise DataContractError("payload contains duplicate game IDs")
    if any(game.get("game_date") != payload.get("date") for game in games):
        raise DataContractError("payload contains a cross-date game")
    if any(
        not isinstance(game.get("weather"), dict)
        or game["weather"].get("game_pk") != game.get("game_pk")
        for game in games
    ):
        raise DataContractError("payload contains weather attached to the wrong game")
    if payload.get("status") == "no_slate" and games:
        raise DataContractError("no-slate payload cannot contain games")
    if payload.get("status") != "no_slate" and not games:
        raise DataContractError("non-empty status requires at least one game")
    health = payload["health"]
    if health["schedule"].get("game_count") != len(games):
        raise DataContractError("schedule health count does not match the slate")
    if payload.get("status") == "no_slate":
        if not payload.get("no_slate_reason"):
            raise DataContractError("no-slate payload requires an explanation")
        if health["weather"].get("state") != "not_applicable" or any(
            health["weather"].get(key) != 0 for key in ("verified_games", "held_games")
        ):
            raise DataContractError("no-slate weather health must be not applicable")
        if (
            health["lineups"].get("state") != "not_applicable"
            or health["lineups"].get("confirmed_games") != 0
        ):
            raise DataContractError("no-slate lineup health must be not applicable")
        return
    if payload.get("no_slate_reason") is not None:
        raise DataContractError("scheduled-slate payload cannot contain a no-slate explanation")

    verified_weather = 0
    confirmed_lineups = 0
    unavailable_lineups = 0
    for game in games:
        weather_state = game["weather"]["state"]
        factor_state = game["factors"]["state"]
        lineup_state = game["lineup"]["state"]
        approach_c_state = game["approach_c"]["state"]
        trajectory_state = game["trajectory"]["state"]
        if weather_state == "verified":
            verified_weather += 1
            if factor_state != "modeled" or trajectory_state != "available":
                raise DataContractError(
                    "verified weather must produce modeled factors and an available trajectory"
                )
        else:
            if factor_state != "held" or trajectory_state != "held":
                raise DataContractError(
                    "degraded weather must hold factors and the trajectory comparison"
                )
            factors = game["factors"]
            if (
                factors["weather_multiplier_runs"] != 1.0
                or factors["weather_multiplier_hr"] != 1.0
                or factors["game_pf_runs"] != factors["seasonal_pf_runs"]
                or factors["game_pf_hr"] != factors["seasonal_pf_hr"]
                or factors["weather_delta_runs"] != 0.0
                or factors["weather_delta_hr"] != 0.0
                or game["trajectory"]["arcs"]
            ):
                raise DataContractError(
                    "degraded weather must expose unchanged seasonal baselines"
                )
        if lineup_state == "confirmed":
            confirmed_lineups += 1
            if game["lineup"]["home_count"] != 9 or game["lineup"]["away_count"] != 9:
                raise DataContractError("confirmed lineups require two complete batting orders")
        elif lineup_state == "unavailable":
            unavailable_lineups += 1
        if approach_c_state == "experimental" and (
            lineup_state != "confirmed" or weather_state != "verified"
        ):
            raise DataContractError(
                "experimental lineup physics requires confirmed lineups and verified weather"
            )
        if approach_c_state == "experimental" and any(
            key not in game["approach_c"]
            for key in (
                "home_hr_index",
                "away_hr_index",
                "home_minus_away",
                "home_profile_coverage",
                "away_profile_coverage",
            )
        ):
            raise DataContractError("experimental lineup physics requires complete metrics")

    expected_status = "ready" if verified_weather == len(games) else "degraded"
    if payload.get("status") != expected_status:
        raise DataContractError("payload status does not match game-level weather health")
    if health["weather"].get("verified_games") != verified_weather:
        raise DataContractError("weather health count does not match the slate")
    if health["weather"].get("held_games") != len(games) - verified_weather:
        raise DataContractError("held-weather count does not match the slate")
    expected_weather_state = (
        "available"
        if verified_weather == len(games)
        else "unavailable"
        if verified_weather == 0
        else "partial"
    )
    if health["weather"].get("state") != expected_weather_state:
        raise DataContractError("weather health state does not match the slate")
    if health["lineups"].get("confirmed_games") != confirmed_lineups:
        raise DataContractError("lineup health count does not match the slate")
    expected_lineup_state = (
        "available"
        if confirmed_lineups == len(games)
        else "partial"
        if confirmed_lineups > 0
        else "unavailable"
        if unavailable_lineups == len(games)
        else "not_yet_available"
    )
    if health["lineups"].get("state") != expected_lineup_state:
        raise DataContractError("lineup health state does not match the slate")
