from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from ballpark.errors import ArtifactError
from ballpark.model import ParkFactorModel
from ballpark.venues import VENUES
from ballpark.weather import neutral_weather
from tests.support import valid_weather


class Booster:
    def __init__(self, value: float):
        self.value = value
        self.calls = 0

    def predict(self, _matrix: object) -> np.ndarray:
        self.calls += 1
        return np.array([self.value], dtype=float)


def _model(*, runs: float = 1.0, hr: float = 1.0) -> ParkFactorModel:
    model = object.__new__(ParkFactorModel)
    model.models = {"runs": Booster(runs), "hr": Booster(hr)}
    model.hr_baselines = {team: 1.04 for team in VENUES}
    model.hr_baseline_as_of = "2026-08-26"
    model.hr_baseline_source = "public seasonal receipt"
    return model


def test_unverified_weather_holds_at_seasonal_baselines_without_model_inference() -> None:
    model = _model(runs=1.2, hr=0.8)
    weather = neutral_weather(810001, VENUES["BOS"], "weather unavailable")

    factors = model.predict(
        target_date=date(2026, 8, 26), venue=VENUES["BOS"], weather=weather
    )

    assert factors["state"] == "held"
    assert factors["game_pf_runs"] == factors["seasonal_pf_runs"]
    assert factors["game_pf_hr"] == factors["seasonal_pf_hr"]
    assert factors["weather_delta_runs"] == 0.0
    assert model.models["runs"].calls == 0
    assert model.models["hr"].calls == 0


def test_verified_weather_clips_model_multipliers() -> None:
    model = _model(runs=4.0, hr=0.1)

    factors = model.predict(
        target_date=date(2026, 8, 26),
        venue=VENUES["BOS"],
        weather=valid_weather(),
    )

    assert factors["state"] == "modeled"
    assert factors["weather_multiplier_runs"] == 1.4
    assert factors["weather_multiplier_hr"] == 0.7


def test_active_dome_forces_neutral_weather_multiplier() -> None:
    model = _model(runs=1.3, hr=0.8)
    weather = valid_weather()
    weather["dome_active"] = True

    factors = model.predict(
        target_date=date(2026, 8, 26), venue=VENUES["TB"], weather=weather
    )

    assert factors["weather_multiplier_runs"] == 1.0
    assert factors["weather_multiplier_hr"] == 1.0
    assert factors["weather_delta_runs"] == 0.0
    assert factors["weather_delta_hr"] == 0.0


def test_missing_hr_baseline_rejects_model_output() -> None:
    model = _model()
    model.hr_baselines.pop("BOS")
    with pytest.raises(ArtifactError, match="HR baseline is missing for BOS"):
        model.predict(
            target_date=date(2026, 8, 26),
            venue=VENUES["BOS"],
            weather=valid_weather(),
        )
