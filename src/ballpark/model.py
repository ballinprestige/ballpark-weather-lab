from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import xgboost as xgb

from ballpark.errors import ArtifactError
from ballpark.venues import Venue

FEATURE_COLUMNS = [
    "temperature_f",
    "humidity_pct",
    "wind_speed_mph",
    "wind_carry_mph",
    "wind_cross_mph",
    "air_density_index",
    "pressure_hpa",
    "altitude_ft",
    "dome_type",
    "dome_active",
    "month",
    "temp_x_wind_carry",
    "temp_x_altitude",
]


class ParkFactorModel:
    def __init__(self, models_dir: Path, data_dir: Path):
        self.models: dict[str, xgb.Booster] = {}
        for name in ("runs", "hr"):
            path = models_dir / f"{name}_weather_model.json"
            try:
                booster = xgb.Booster()
                booster.load_model(str(path))
            except Exception as exc:
                raise ArtifactError(f"{name} weather model is malformed") from exc
            self.models[name] = booster
        try:
            baseline_doc = json.loads(
                (data_dir / "hr_baselines_2026.json").read_text(encoding="utf-8-sig")
            )
            self.hr_baselines = {
                team: float(row["seasonal_pf_hr"])
                for team, row in baseline_doc["venues"].items()
            }
            self.hr_baseline_as_of = str(baseline_doc["baseline_as_of"])
            self.hr_baseline_source = str(baseline_doc["source_url"])
        except Exception as exc:
            raise ArtifactError("HR baseline receipt is malformed") from exc

    def predict(
        self,
        *,
        target_date: date,
        venue: Venue,
        weather: dict[str, Any],
    ) -> dict[str, Any]:
        seasonal_runs = venue.seasonal_pf_runs
        seasonal_hr = self.hr_baselines.get(venue.team)
        if seasonal_hr is None:
            raise ArtifactError(f"HR baseline is missing for {venue.team}")

        if weather.get("state") != "verified":
            return {
                "state": "held",
                "reason": "game-hour weather is not verified; seasonal baselines remain visible",
                "seasonal_pf_runs": round(seasonal_runs, 4),
                "seasonal_pf_hr": round(seasonal_hr, 4),
                "weather_multiplier_runs": 1.0,
                "weather_multiplier_hr": 1.0,
                "game_pf_runs": round(seasonal_runs, 4),
                "game_pf_hr": round(seasonal_hr, 4),
                "weather_delta_runs": 0.0,
                "weather_delta_hr": 0.0,
                "hr_baseline_as_of": self.hr_baseline_as_of,
            }

        values = {
            "temperature_f": float(weather["temperature_f"]),
            "humidity_pct": float(weather["humidity_pct"]),
            "wind_speed_mph": float(weather["wind_speed_mph"]),
            "wind_carry_mph": float(weather["wind_carry_mph"]),
            "wind_cross_mph": float(weather["wind_cross_mph"]),
            "air_density_index": float(weather["air_density_index"]),
            "pressure_hpa": float(weather["pressure_hpa"]),
            "altitude_ft": float(venue.altitude_ft),
            "dome_type": float(venue.dome_type),
            "dome_active": 1.0 if weather.get("dome_active") else 0.0,
            "month": float(target_date.month),
        }
        values["temp_x_wind_carry"] = values["temperature_f"] * values["wind_carry_mph"]
        values["temp_x_altitude"] = values["temperature_f"] * values["altitude_ft"]
        matrix = xgb.DMatrix(
            np.array([[values[name] for name in FEATURE_COLUMNS]], dtype=np.float64),
            feature_names=FEATURE_COLUMNS,
        )
        runs_multiplier = float(np.clip(self.models["runs"].predict(matrix)[0], 0.70, 1.40))
        hr_multiplier = float(np.clip(self.models["hr"].predict(matrix)[0], 0.70, 1.40))
        if weather.get("dome_active"):
            runs_multiplier = 1.0
            hr_multiplier = 1.0
        game_runs = seasonal_runs * runs_multiplier
        game_hr = seasonal_hr * hr_multiplier
        return {
            "state": "modeled",
            "reason": None,
            "seasonal_pf_runs": round(seasonal_runs, 4),
            "seasonal_pf_hr": round(seasonal_hr, 4),
            "weather_multiplier_runs": round(runs_multiplier, 4),
            "weather_multiplier_hr": round(hr_multiplier, 4),
            "game_pf_runs": round(game_runs, 4),
            "game_pf_hr": round(game_hr, 4),
            "weather_delta_runs": round(game_runs - seasonal_runs, 4),
            "weather_delta_hr": round(game_hr - seasonal_hr, 4),
            "hr_baseline_as_of": self.hr_baseline_as_of,
        }
