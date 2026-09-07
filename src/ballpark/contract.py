from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from ballpark.errors import DataContractError
from ballpark.kalshi import _KALSHI_TEAM, _event_start

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
    if any(isinstance(game, dict) and "odds" in game for game in games) and "odds" not in health:
        raise DataContractError("market-bearing payload requires a complete odds health lane")
    if any(isinstance(game, dict) and "exchange_market" in game for game in games) and "exchange_markets" not in health:
        raise DataContractError("exchange-bearing payload requires a complete exchange health lane")
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
        if "odds" in health and health["odds"].get("state") != "not_applicable":
            raise DataContractError("no-slate odds health must be not applicable")
        if "exchange_markets" in health and health["exchange_markets"].get("state") != "not_applicable":
            raise DataContractError("no-slate exchange health must be not applicable")
        return
    if payload.get("no_slate_reason") is not None:
        raise DataContractError("scheduled-slate payload cannot contain a no-slate explanation")

    verified_weather = 0
    modeled_factors = 0
    confirmed_lineups = 0
    unavailable_lineups = 0
    odds_states: list[str] = []
    exchange_states: list[str] = []
    for game in games:
        weather_state = game["weather"]["state"]
        factor_state = game["factors"]["state"]
        lineup_state = game["lineup"]["state"]
        approach_c_state = game["approach_c"]["state"]
        trajectory_state = game["trajectory"]["state"]
        factors = game["factors"]
        held_baselines = (
            factors["weather_multiplier_runs"] == 1.0
            and factors["weather_multiplier_hr"] == 1.0
            and factors["game_pf_runs"] == factors["seasonal_pf_runs"]
            and factors["game_pf_hr"] == factors["seasonal_pf_hr"]
            and factors["weather_delta_runs"] == 0.0
            and factors["weather_delta_hr"] == 0.0
        )
        if weather_state == "verified":
            verified_weather += 1
            if trajectory_state != "available":
                raise DataContractError(
                    "verified weather must produce an available trajectory"
                )
            if factor_state == "held":
                if not isinstance(factors["reason"], str) or not factors["reason"].strip():
                    raise DataContractError(
                        "verified weather with held factors requires a model-validation reason"
                    )
                if not held_baselines:
                    raise DataContractError(
                        "verified weather with held factors must expose unchanged seasonal "
                        "baselines"
                    )
            elif factor_state != "modeled":
                raise DataContractError("verified weather has an invalid factor state")
            else:
                modeled_factors += 1
        else:
            if factor_state != "held" or trajectory_state != "held":
                raise DataContractError(
                    "degraded weather must hold factors and the trajectory comparison"
                )
            if not held_baselines or game["trajectory"]["arcs"]:
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
        if "odds" in game:
            odds = game["odds"]
            if odds.get("game_pk") != game.get("game_pk") or odds.get("slate_date") != payload.get("date"):
                raise DataContractError("payload contains odds attached to the wrong game or date")
            if odds.get("sport") != "MLB" or odds.get("market_type") != "total" or odds.get("period") != "full_game":
                raise DataContractError("payload contains a non-MLB full-game-total market")
            state = odds.get("state")
            odds_states.append(state)
            populated = any(odds.get(key) is not None for key in ("line", "over_price", "under_price", "raw_sha256", "snapshot_id"))
            if populated:
                required_observed = ("line", "over_price", "under_price", "observed_at", "raw_sha256", "snapshot_id", "sportsbook_id", "sportsbook_name", "provider_event_id")
                if any(odds.get(key) is None for key in required_observed):
                    raise DataContractError("observed market is missing price, provenance, book, or retrieval evidence")
                for price_key in ("over_price", "under_price"):
                    price = odds[price_key]
                    if not isinstance(price, int) or isinstance(price, bool) or -99 <= price <= 99:
                        raise DataContractError("quoted market has an invalid American price")
                observed = datetime.fromisoformat(str(odds["observed_at"]).replace("Z", "+00:00")).astimezone(UTC)
                generated = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00")).astimezone(UTC)
                if observed > generated + timedelta(minutes=5):
                    raise DataContractError("market retrieval is implausibly later than publication")
            if state in {"current", "stale"}:
                required = ("line", "over_price", "under_price", "source_updated_at", "observed_at", "raw_sha256", "snapshot_id", "sportsbook_id", "sportsbook_name", "provider_event_id")
                if any(odds.get(key) is None for key in required):
                    raise DataContractError("quoted market is missing line, both prices, provenance, or timestamps")
                for price_key in ("over_price", "under_price"):
                    price = odds[price_key]
                    if not isinstance(price, int) or -99 <= price <= 99:
                        raise DataContractError("quoted market has an invalid American price")
            elif state != "unavailable":
                raise DataContractError("odds market has an unknown state")
        if "exchange_market" in game:
            exchange = game["exchange_market"]
            if exchange.get("game_pk") != game.get("game_pk") or exchange.get("game_time") != game.get("game_time") or exchange.get("slate_date") != payload.get("date"):
                raise DataContractError("payload contains exchange market attached to the wrong game or start")
            state = exchange.get("state")
            exchange_states.append(state)
            if exchange.get("provider") != "Kalshi" or exchange.get("market_type") != "total" or exchange.get("period") != "full_game" or exchange.get("quote_type") != "contract_ask" or exchange.get("price_format") != "contract_cents":
                raise DataContractError("exchange market has an invalid provider or price domain")
            if (state == "observed_unknown_age") != (exchange.get("active") is True):
                raise DataContractError("exchange active flag does not match its availability state")
            start_clock = datetime.fromisoformat(
                str(game["game_time"]).replace("Z", "+00:00")
            ).astimezone(UTC)
            status = str(game.get("game_status") or "").lower()
            phase = exchange.get("game_phase")
            if any(word in status for word in ("final", "game over", "completed", "closed")):
                expected_phase = "final"
            elif any(word in status for word in ("in progress", "live", "suspended")):
                expected_phase = "in_progress"
            elif any(word in status for word in ("postponed", "cancelled", "canceled", "delayed")):
                expected_phase = "unknown"
            else:
                assessment_time = payload["generated_at"] if exchange.get("failure_reason") else exchange["observed_at"]
                observed_clock = datetime.fromisoformat(
                    str(assessment_time).replace("Z", "+00:00")
                ).astimezone(UTC)
                expected_phase = "pregame" if observed_clock < start_clock else "after_scheduled_start"
            if phase != expected_phase:
                raise DataContractError("exchange phase does not match official status and scheduled start")
            if state == "observed_unknown_age":
                required = ("event_ticker", "market_ticker", "condition", "line", "over_ask_dollars", "under_ask_dollars", "over_ask_cents", "under_ask_cents", "over_ask_size", "under_ask_size", "observed_at", "raw_sha256", "snapshot_id")
                if any(exchange.get(key) is None for key in required) or exchange.get("source_updated_at") is not None:
                    raise DataContractError("observed exchange quote is missing evidence or invents a source update time")
                parsed = _event_start(exchange.get("event_ticker"))
                teams = (
                    _KALSHI_TEAM.get(str(game.get("away_team")), game.get("away_team")),
                    _KALSHI_TEAM.get(str(game.get("home_team")), game.get("home_team")),
                )
                if not parsed or parsed[0] != start_clock or parsed[1:] != teams:
                    raise DataContractError("exchange ticker does not match the official game identity")
                try:
                    over, under = Decimal(str(exchange["over_ask_dollars"])), Decimal(str(exchange["under_ask_dollars"]))
                    over_size, under_size = Decimal(str(exchange["over_ask_size"])), Decimal(str(exchange["under_ask_size"]))
                except (InvalidOperation, ValueError) as exc:
                    raise DataContractError("exchange quote has invalid decimal prices or depth") from exc
                if not (Decimal("0") < over < Decimal("1") and Decimal("0") < under < Decimal("1") and over_size > 0 and under_size > 0):
                    raise DataContractError("exchange quote requires positive two-sided asks and depth")
                if (
                    Decimal(str(exchange["over_ask_cents"])) != over * 100
                    or Decimal(str(exchange["under_ask_cents"])) != under * 100
                ):
                    raise DataContractError("exchange display cents do not match native dollars")
                observed = datetime.fromisoformat(str(exchange["observed_at"]).replace("Z", "+00:00")).astimezone(UTC)
                generated = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00")).astimezone(UTC)
                if observed > generated + timedelta(minutes=5):
                    raise DataContractError("exchange retrieval is implausibly later than publication")
            elif state != "unavailable":
                raise DataContractError("exchange market has an unknown state")

    expected_status = (
        "ready"
        if verified_weather == len(games) and modeled_factors == len(games)
        else "degraded"
    )
    if payload.get("status") != expected_status:
        raise DataContractError("payload status does not match weather and factor availability")
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
    if "odds" in health:
        if len(odds_states) != len(games):
            raise DataContractError("odds health exists but one or more games have no market state")
        counts = {state: odds_states.count(state) for state in ("current", "stale", "unavailable")}
        if any(health["odds"].get(f"{state}_games") != count for state, count in counts.items()):
            raise DataContractError("odds health counts do not match game market states")
        expected_odds_state = "available" if counts["current"] == len(games) else "partial" if counts["current"] else "unavailable"
        if health["odds"].get("state") != expected_odds_state:
            raise DataContractError("odds health state does not match game market states")
    if "exchange_markets" in health:
        if len(exchange_states) != len(games):
            raise DataContractError("exchange health exists but one or more games have no exchange state")
        observed_count = exchange_states.count("observed_unknown_age")
        unavailable_count = exchange_states.count("unavailable")
        if health["exchange_markets"].get("observed_unknown_age_games") != observed_count or health["exchange_markets"].get("unavailable_games") != unavailable_count:
            raise DataContractError("exchange health counts do not match game market states")
        expected_exchange_state = "available" if observed_count == len(games) else "partial" if observed_count else "unavailable"
        if health["exchange_markets"].get("state") != expected_exchange_state:
            raise DataContractError("exchange health state does not match game market states")
