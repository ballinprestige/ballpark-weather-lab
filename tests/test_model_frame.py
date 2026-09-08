from __future__ import annotations

import json
from datetime import date
from typing import Any

import numpy as np

from ballpark.model import FEATURE_COLUMNS, REVIEWED_ARTIFACT_SHA256, ParkFactorModel
from ballpark.venues import VENUES
from ballpark.weather import decompose_wind, model_frame_0deg_for_weather, parse_forecast


def _forecast(*, temperature: float, speed: float, direction: float) -> dict[str, Any]:
    return {
        "hourly": {
            "time": ["2026-08-26T23:00:00Z"],
            "temperature_2m": [temperature],
            "relative_humidity_2m": [54.0],
            "wind_speed_10m": [speed],
            "wind_direction_10m": [direction],
            "surface_pressure": [1008.4],
        }
    }


def _weather(*, temperature: float, speed: float, direction: float) -> dict[str, Any]:
    return parse_forecast(
        _forecast(temperature=temperature, speed=speed, direction=direction),
        game_pk=810001,
        game_time="2026-08-26T23:10:00Z",
        venue=VENUES["LAD"],
    )


def test_model_frame_uses_unrounded_source_while_public_lad_weather_stays_physical() -> None:
    weather = _weather(temperature=71.24, speed=7.3, direction=259.0)
    model = model_frame_0deg_for_weather(weather)

    assert (weather["wind_carry_mph"], weather["wind_cross_mph"]) == (4.19, 5.98)
    assert model is not None
    assert (model["wind_speed_mph"], model["wind_carry_mph"], model["wind_cross_mph"]) == (
        7.3,
        1.39,
        7.17,
    )
    assert model["temperature_f"] == 71.2
    assert set(weather) == {
        "game_pk", "state", "source", "basis", "reason", "valid_at", "fetched_at",
        "temperature_f", "humidity_pct", "wind_speed_mph", "wind_direction_deg",
        "wind_carry_mph", "wind_cross_mph", "air_density_index", "pressure_hpa",
        "dome_active", "roof_state",
    }
    assert "model_frame_0deg" not in json.dumps(weather)


def test_model_frame_reproduces_retained_lad_source_sample() -> None:
    weather = _weather(temperature=68.2, speed=6.21371, direction=155.0)
    model = model_frame_0deg_for_weather(weather)

    assert (weather["wind_carry_mph"], weather["wind_cross_mph"]) == (4.08, -4.69)
    assert model is not None
    assert (model["wind_speed_mph"], model["wind_carry_mph"], model["wind_cross_mph"]) == (
        6.21,
        5.63,
        -2.63,
    )


def test_model_frame_rounds_after_component_construction_not_public_rounding() -> None:
    weather = _weather(temperature=70.0, speed=10.05, direction=200.05)
    model = model_frame_0deg_for_weather(weather)

    assert weather["wind_speed_mph"] == 10.1
    assert weather["wind_direction_deg"] == 200.1
    assert model is not None
    assert (model["wind_carry_mph"], model["wind_cross_mph"]) == (9.44, 3.45)
    assert decompose_wind(10.1, 200.1, 0.0) == (9.48, 3.47)


class _RecordingBooster:
    def __init__(self) -> None:
        self.calls = 0
        self.matrix: Any = None

    def predict(self, matrix: Any) -> np.ndarray:
        self.calls += 1
        self.matrix = matrix
        return np.array([1.0], dtype=float)


def _model() -> ParkFactorModel:
    model = object.__new__(ParkFactorModel)
    model.models = {"runs": _RecordingBooster(), "hr": _RecordingBooster()}
    model.hr_baselines = {team: 1.04 for team in VENUES}
    model.hr_baseline_as_of = "2026-08-26"
    model.hr_baseline_source = "fixture"
    return model


def test_park_factor_model_consumes_model_frame_and_not_public_physical_components() -> None:
    weather = _weather(temperature=71.24, speed=7.3, direction=259.0)
    model = _model()

    factors = model.predict(target_date=date(2026, 8, 26), venue=VENUES["BOS"], weather=weather)

    assert factors["state"] == "modeled"
    frame = model.models["runs"].matrix.get_data().toarray()[0]
    values = dict(zip(FEATURE_COLUMNS, frame, strict=True))
    assert values["wind_carry_mph"] == 1.39
    assert values["wind_cross_mph"] == 7.17
    assert values["temp_x_wind_carry"] == 71.2 * 1.39
    assert (weather["wind_carry_mph"], weather["wind_cross_mph"]) == (4.19, 5.98)


def test_public_or_deserialized_weather_cannot_be_reused_for_model_scoring() -> None:
    weather = _weather(temperature=71.24, speed=7.3, direction=259.0)
    model = _model()

    factors = model.predict(
        target_date=date(2026, 8, 26), venue=VENUES["BOS"], weather=dict(weather)
    )

    assert factors["state"] == "held"
    assert "selected unrounded source tuple" in factors["reason"]
    assert model.models["runs"].calls == 0
    assert model.models["hr"].calls == 0


def test_reviewed_saved_artifacts_pin_manifest_and_booster_feature_identity(project_paths) -> None:
    model = ParkFactorModel(project_paths.models, project_paths.data)

    assert REVIEWED_ARTIFACT_SHA256["training_manifest.json"]
    assert model.models["runs"].feature_names == FEATURE_COLUMNS
    assert model.models["hr"].feature_names == FEATURE_COLUMNS
