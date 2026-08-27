from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from ballpark.http import HttpClient
from ballpark.venues import Venue

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
INDOOR_TEMP_F = 72.0
INDOOR_HUMIDITY_PCT = 50.0
R_D = 287.058
R_V = 461.495
RHO_ISA = 1.225


def air_density_index(temp_f: float, humidity_pct: float, altitude_ft: float) -> float:
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    temp_k = temp_c + 273.15
    pressure_hpa = 1013.25 * (1 - 2.25577e-5 * (altitude_ft * 0.3048)) ** 5.25588
    e_sat = 6.1078 * (10 ** ((7.5 * temp_c) / (temp_c + 237.3)))
    vapor_pressure = e_sat * (humidity_pct / 100.0)
    dry_pressure = pressure_hpa - vapor_pressure
    rho = (dry_pressure * 100) / (R_D * temp_k) + (vapor_pressure * 100) / (R_V * temp_k)
    return round((rho / RHO_ISA) * 100.0, 2)


def decompose_wind(
    wind_speed_mph: float, wind_direction_deg: float, center_field_azimuth: float
) -> tuple[float, float]:
    angle = math.radians(wind_direction_deg - (center_field_azimuth + 180.0))
    return round(wind_speed_mph * math.cos(angle), 2), round(
        wind_speed_mph * math.sin(angle), 2
    )


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def neutral_weather(game_pk: int, venue: Venue, reason: str) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "state": "degraded",
        "source": "neutral_fallback",
        "basis": "neutral",
        "reason": reason,
        "valid_at": None,
        "fetched_at": _iso_now(),
        "temperature_f": 68.0,
        "humidity_pct": 50.0,
        "wind_speed_mph": 0.0,
        "wind_direction_deg": None,
        "wind_carry_mph": 0.0,
        "wind_cross_mph": 0.0,
        "air_density_index": air_density_index(68.0, 50.0, venue.altitude_ft),
        "pressure_hpa": 1013.25,
        "dome_active": False,
        "roof_state": "unknown",
    }


def indoor_weather(game_pk: int, venue: Venue) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "state": "verified",
        "source": "venue_registry",
        "basis": "indoor",
        "reason": None,
        "valid_at": None,
        "fetched_at": _iso_now(),
        "temperature_f": INDOOR_TEMP_F,
        "humidity_pct": INDOOR_HUMIDITY_PCT,
        "wind_speed_mph": 0.0,
        "wind_direction_deg": None,
        "wind_carry_mph": 0.0,
        "wind_cross_mph": 0.0,
        "air_density_index": air_density_index(
            INDOOR_TEMP_F, INDOOR_HUMIDITY_PCT, venue.altitude_ft
        ),
        "pressure_hpa": 1013.25,
        "dome_active": True,
        "roof_state": "fixed-roof",
    }


def _parse_utc(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_forecast(
    document: Any,
    *,
    game_pk: int,
    game_time: str,
    venue: Venue,
) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("hourly"), dict):
        raise ValueError("Open-Meteo response omits hourly weather")
    hourly = document["hourly"]
    required = (
        "time",
        "temperature_2m",
        "relative_humidity_2m",
        "wind_speed_10m",
        "wind_direction_10m",
        "surface_pressure",
    )
    values = {name: hourly.get(name) for name in required}
    if not all(isinstance(value, list) and value for value in values.values()):
        raise ValueError("Open-Meteo hourly weather is incomplete")
    lengths = {len(value) for value in values.values() if isinstance(value, list)}
    if len(lengths) != 1:
        raise ValueError("Open-Meteo hourly weather arrays have different lengths")

    target = _parse_utc(game_time)
    candidates = [_parse_utc(str(raw)) for raw in values["time"]]
    index = min(range(len(candidates)), key=lambda i: abs((candidates[i] - target).total_seconds()))
    distance_seconds = abs((candidates[index] - target).total_seconds())
    if distance_seconds > 90 * 60:
        raise ValueError("Open-Meteo response has no forecast within 90 minutes of game time")
    temp_f = float(values["temperature_2m"][index])
    humidity = float(values["relative_humidity_2m"][index])
    wind_speed = float(values["wind_speed_10m"][index])
    wind_direction = float(values["wind_direction_10m"][index])
    pressure = float(values["surface_pressure"][index])
    measurements = (temp_f, humidity, wind_speed, wind_direction, pressure)
    if not all(math.isfinite(value) for value in measurements):
        raise ValueError("Open-Meteo game-hour weather contains a non-finite value")
    if not -80 <= temp_f <= 150:
        raise ValueError("Open-Meteo game-hour temperature is out of range")
    if not 0 <= humidity <= 100:
        raise ValueError("Open-Meteo game-hour humidity is out of range")
    if not 0 <= wind_speed <= 250 or not 0 <= wind_direction <= 360:
        raise ValueError("Open-Meteo game-hour wind is out of range")
    if not 500 <= pressure <= 1200:
        raise ValueError("Open-Meteo game-hour pressure is out of range")
    carry, cross = decompose_wind(wind_speed, wind_direction, venue.center_field_azimuth)
    return {
        "game_pk": game_pk,
        "state": "verified",
        "source": "open-meteo",
        "basis": "forecast",
        "reason": None,
        "valid_at": candidates[index].isoformat().replace("+00:00", "Z"),
        "fetched_at": _iso_now(),
        "temperature_f": round(temp_f, 1),
        "humidity_pct": round(humidity, 1),
        "wind_speed_mph": round(wind_speed, 1),
        "wind_direction_deg": round(wind_direction, 1),
        "wind_carry_mph": carry,
        "wind_cross_mph": cross,
        "air_density_index": air_density_index(temp_f, humidity, venue.altitude_ft),
        "pressure_hpa": round(pressure, 1),
        "dome_active": False,
        "roof_state": "unconfirmed" if venue.dome_type == 1 else "open-air",
    }


def fetch_game_weather(game: dict[str, Any], venue: Venue, client: HttpClient) -> dict[str, Any]:
    game_pk = int(game["game_pk"])
    if venue.dome_type == 2:
        return indoor_weather(game_pk, venue)
    if not game.get("game_time"):
        return neutral_weather(game_pk, venue, "schedule has no game time")
    try:
        document = client.get_json(
            FORECAST_URL,
            params={
                "latitude": venue.latitude,
                "longitude": venue.longitude,
                "hourly": (
                    "temperature_2m,relative_humidity_2m,wind_speed_10m,"
                    "wind_direction_10m,surface_pressure"
                ),
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "UTC",
                "forecast_days": 2,
                "past_days": 1,
            },
        )
        return parse_forecast(document, game_pk=game_pk, game_time=game["game_time"], venue=venue)
    except Exception as exc:
        return neutral_weather(game_pk, venue, f"game-hour weather unavailable: {exc}")
