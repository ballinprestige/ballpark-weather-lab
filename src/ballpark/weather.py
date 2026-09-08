from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
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
MODEL_SOURCE_RECEIPT_SCHEMA_VERSION = 1
MODEL_SOURCE_RECEIPT_TRANSFORM = "model_frame_0deg-v1"


@dataclass(frozen=True)
class ModelFrame0Weather:
    """Private model-input receipt for the legacy 0-degree saved-model frame.

    This is intentionally not a mapping key. Public payload weather remains in the
    reviewed physical venue frame and contains presentation-rounded values only.
    """

    temperature_f: float
    wind_speed_mph: float
    wind_direction_deg: float
    provenance: str = "selected_provider_tuple_unrounded_model_frame_0deg"


class WeatherPayload(dict[str, Any]):
    """Public weather mapping with an in-process-only model source receipt."""

    model_frame_0deg: ModelFrame0Weather | None

    def __init__(
        self, values: dict[str, Any], *, model_frame_0deg: ModelFrame0Weather | None = None
    ) -> None:
        super().__init__(values)
        self.model_frame_0deg = model_frame_0deg


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
    return round(wind_speed_mph * math.cos(angle), 2), round(wind_speed_mph * math.sin(angle), 2)


def model_frame_0deg_from_source(
    *, temperature_f: float, wind_speed_mph: float, wind_direction_deg: float
) -> ModelFrame0Weather:
    """Capture the selected provider tuple before public weather rounding.

    The saved artifacts were trained with ``cf_azimuth_deg=0``.  This receipt is
    the sole accepted source for their carry/cross inputs; physical venue fields
    are intentionally not used to reconstruct it.
    """
    values = (temperature_f, wind_speed_mph, wind_direction_deg)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("model-frame weather source contains a non-finite value")
    if not -80 <= temperature_f <= 150 or not 0 <= wind_speed_mph <= 250:
        raise ValueError("model-frame weather source is out of range")
    if not 0 <= wind_direction_deg <= 360:
        raise ValueError("model-frame wind direction is out of range")
    return ModelFrame0Weather(
        # Historical temperature is a one-decimal feature.  This is distinct from
        # speed/direction, which must stay unrounded until their components exist.
        temperature_f=round(temperature_f, 1),
        wind_speed_mph=wind_speed_mph,
        wind_direction_deg=wind_direction_deg,
    )


def model_frame_0deg_components(source: ModelFrame0Weather) -> dict[str, float | str]:
    """Build legacy model features directly from the retained raw wind tuple."""
    carry, cross = decompose_wind(source.wind_speed_mph, source.wind_direction_deg, 0.0)
    speed = round(source.wind_speed_mph, 2)
    return {
        "temperature_f": source.temperature_f,
        "wind_speed_mph": speed,
        "wind_carry_mph": carry,
        "wind_cross_mph": cross,
        "temp_x_wind_carry": source.temperature_f * carry,
        "provenance": source.provenance,
    }


def model_frame_0deg_for_weather(weather: dict[str, Any]) -> dict[str, float | str] | None:
    """Return private model features, never reconstructed from public fields."""
    source = getattr(weather, "model_frame_0deg", None)
    if not isinstance(source, ModelFrame0Weather):
        return None
    return model_frame_0deg_components(source)


def _canonical_json_bytes(value: Any) -> bytes:
    """Canonical bytes for internal integrity bindings (not source authentication)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _model_contract() -> dict[str, Any]:
    """Return the exact saved-model contract without making weather import model eagerly."""
    # model imports this module.  Keeping this import local prevents an import cycle while
    # still binding a durable receipt to the reviewed artifact and ordered feature identity.
    from ballpark.model import FEATURE_COLUMNS, REVIEWED_ARTIFACT_SHA256

    return {
        "feature_columns": list(FEATURE_COLUMNS),
        "artifact_sha256": dict(REVIEWED_ARTIFACT_SHA256),
    }


def _public_weather_identity(weather: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the observation identity required to bind a private model receipt."""
    game_pk = weather.get("game_pk")
    source = weather.get("source")
    basis = weather.get("basis")
    valid_at = weather.get("valid_at")
    fetched_at = weather.get("fetched_at")
    if (
        isinstance(game_pk, bool)
        or not isinstance(game_pk, int)
        or not isinstance(source, str)
        or not isinstance(basis, str)
        or valid_at is not None
        and not isinstance(valid_at, str)
        or not isinstance(fetched_at, str)
    ):
        return None
    return {
        "game_pk": game_pk,
        "source": source,
        "basis": basis,
        "valid_at": valid_at,
        "fetched_at": fetched_at,
    }


def build_model_source_receipt(weather: dict[str, Any]) -> dict[str, Any] | None:
    """Create a durable *internal* receipt from an in-process selected source tuple.

    The receipt deliberately cannot be made from a plain public weather mapping: its raw
    tuple comes only from ``WeatherPayload.model_frame_0deg`` captured at acquisition.
    Its digest protects cache integrity and observation binding; it does not authenticate a
    provider or make a generic external fixture a trusted source.
    """
    identity = _public_weather_identity(weather)
    source = getattr(weather, "model_frame_0deg", None)
    if identity is None or not isinstance(source, ModelFrame0Weather):
        return None
    try:
        # Revalidate before persisting so a manually mutated private object cannot become a
        # durable receipt.
        normalized = model_frame_0deg_from_source(
            temperature_f=source.temperature_f,
            wind_speed_mph=source.wind_speed_mph,
            wind_direction_deg=source.wind_direction_deg,
        )
    except (TypeError, ValueError):
        return None
    receipt: dict[str, Any] = {
        "schema_version": MODEL_SOURCE_RECEIPT_SCHEMA_VERSION,
        "transform": MODEL_SOURCE_RECEIPT_TRANSFORM,
        "observation": identity,
        "public_weather_sha256": _sha256(dict(weather)),
        "model_input": {
            # This is the exact one-decimal temperature feature.  Wind remains raw until
            # components are constructed by model_frame_0deg_components.
            "temperature_f": normalized.temperature_f,
            "wind_speed_mph": normalized.wind_speed_mph,
            "wind_direction_deg": normalized.wind_direction_deg,
        },
        "model_contract": _model_contract(),
    }
    receipt["receipt_sha256"] = _sha256(receipt)
    return receipt


def rehydrate_model_source_weather(
    public_weather: dict[str, Any], internal_receipt: Any
) -> WeatherPayload | None:
    """Rehydrate a private model tuple only from a matching durable internal receipt.

    Invalid, missing, public-only, or stale receipts fail closed.  Callers must keep the
    receipt in a private source snapshot and must never place it in the public slate payload.
    """
    if not isinstance(public_weather, dict) or not isinstance(internal_receipt, dict):
        return None
    expected_keys = {
        "schema_version",
        "transform",
        "observation",
        "public_weather_sha256",
        "model_input",
        "model_contract",
        "receipt_sha256",
    }
    if set(internal_receipt) != expected_keys:
        return None
    receipt_sha256 = internal_receipt.get("receipt_sha256")
    unsigned_receipt = {
        key: value for key, value in internal_receipt.items() if key != "receipt_sha256"
    }
    if (
        not isinstance(receipt_sha256, str)
        or len(receipt_sha256) != 64
        or receipt_sha256 != _sha256(unsigned_receipt)
        or internal_receipt.get("schema_version") != MODEL_SOURCE_RECEIPT_SCHEMA_VERSION
        or internal_receipt.get("transform") != MODEL_SOURCE_RECEIPT_TRANSFORM
    ):
        return None
    identity = _public_weather_identity(public_weather)
    if (
        identity is None
        or internal_receipt.get("observation") != identity
        or internal_receipt.get("public_weather_sha256") != _sha256(dict(public_weather))
        or internal_receipt.get("model_contract") != _model_contract()
    ):
        return None
    model_input = internal_receipt.get("model_input")
    if not isinstance(model_input, dict) or set(model_input) != {
        "temperature_f",
        "wind_speed_mph",
        "wind_direction_deg",
    }:
        return None
    try:
        source = model_frame_0deg_from_source(
            temperature_f=model_input["temperature_f"],
            wind_speed_mph=model_input["wind_speed_mph"],
            wind_direction_deg=model_input["wind_direction_deg"],
        )
    except (TypeError, ValueError):
        return None
    # model_frame_0deg_from_source rounds temperature.  A durable receipt must carry the
    # precise model feature, rather than silently accepting a different value.
    if source.temperature_f != model_input["temperature_f"]:
        return None
    return WeatherPayload(dict(public_weather), model_frame_0deg=source)


def build_model_source_receipts(weather_by_game: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Create a private cache map keyed by game id; public maps receive no new fields."""
    receipts: dict[str, dict[str, Any]] = {}
    for game_id, weather in weather_by_game.items():
        receipt = build_model_source_receipt(weather) if isinstance(weather, dict) else None
        if receipt is not None and str(receipt["observation"]["game_pk"]) == str(game_id):
            receipts[str(game_id)] = receipt
    return receipts


def rehydrate_model_source_weather_by_game(
    weather_by_game: dict[str, Any], internal_receipts: Any
) -> dict[str, dict[str, Any]]:
    """Return public mappings, with private tuples restored only from the separate map."""
    if not isinstance(weather_by_game, dict) or not isinstance(internal_receipts, dict):
        return {
            str(game_id): dict(weather)
            for game_id, weather in weather_by_game.items()
            if isinstance(weather, dict)
        }
    hydrated: dict[str, dict[str, Any]] = {}
    for game_id, weather in weather_by_game.items():
        if not isinstance(weather, dict):
            continue
        restored = rehydrate_model_source_weather(weather, internal_receipts.get(str(game_id)))
        hydrated[str(game_id)] = restored if restored is not None else dict(weather)
    return hydrated


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
    values = {
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
    return WeatherPayload(
        values,
        model_frame_0deg=model_frame_0deg_from_source(
            temperature_f=INDOOR_TEMP_F, wind_speed_mph=0.0, wind_direction_deg=0.0
        ),
    )


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
    public_weather = {
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
    return WeatherPayload(
        public_weather,
        model_frame_0deg=model_frame_0deg_from_source(
            temperature_f=temp_f, wind_speed_mph=wind_speed, wind_direction_deg=wind_direction
        ),
    )


def fetch_game_weather(
    game: dict[str, Any], venue: Venue, client: HttpClient, *, deadline_at: float | None = None
) -> dict[str, Any]:
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
            deadline_at=deadline_at,
        )
        return parse_forecast(document, game_pk=game_pk, game_time=game["game_time"], venue=venue)
    except Exception as exc:
        return neutral_weather(game_pk, venue, f"game-hour weather unavailable: {exc}")
