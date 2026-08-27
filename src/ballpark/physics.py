from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ballpark.venues import Venue

G = 32.174
RHO_SEA = 0.07474
BALL_MASS = 0.3203
BALL_CIRC = 9.125 / 12.0
BALL_RADIUS = BALL_CIRC / (2 * math.pi)
BALL_AREA = math.pi * BALL_RADIUS**2
CD_BASE = 0.3008
CL_BASE = 0.2060

EV_BINS = np.arange(60, 122, 2)
LA_BINS = np.arange(-20, 62, 2)
SPRAY_BINS = np.arange(-45, 50, 5)
TEMP_BUCKETS = [47, 57, 67, 77, 87]
ALT_BUCKETS = [30, 500, 800, 1100, 5183]
WIND_BUCKETS = [-8, -4, 0, 4, 8]
PROFILE_PERCENTILES = [5, 25, 50, 75, 95]
SPRAY_BIN_CENTERS = np.arange(-40, 41, 10)
PA_WEIGHTS = [4.63, 4.53, 4.41, 4.31, 4.21, 4.10, 3.99, 3.88, 3.75]


def air_density_ratio(temp_f: float, altitude_ft: float) -> float:
    temp_c = (temp_f - 32.0) * 5.0 / 9.0
    temp_k = temp_c + 273.15
    pressure_ratio = (1 - 2.25577e-5 * altitude_ft * 0.3048) ** 5.25588
    return pressure_ratio * (288.15 / temp_k)


def simulate_trajectory(
    exit_velocity_mph: float,
    launch_angle_deg: float,
    spray_angle_deg: float,
    density_ratio: float = 1.0,
    wind_carry_mph: float = 0.0,
    *,
    dt: float = 0.02,
    max_time: float = 8.0,
) -> tuple[float, float]:
    """Return landing distance and peak height using a bounded Euler flight approximation.

    This preserves the established table generator's actual numerical method. It is deliberately
    described as an approximation rather than the RK4 method claimed by an older docstring.
    """

    velocity = exit_velocity_mph * 5280 / 3600
    wind_carry = wind_carry_mph * 5280 / 3600
    launch = math.radians(launch_angle_deg)
    spray = math.radians(spray_angle_deg)
    vx = velocity * math.cos(launch) * math.sin(spray)
    vy = velocity * math.cos(launch) * math.cos(spray)
    vz = velocity * math.sin(launch)
    x, y, z = 0.0, 0.0, 3.0
    max_height = z
    rho = RHO_SEA * density_ratio
    drag_factor = 0.5 * rho * BALL_AREA * CD_BASE / BALL_MASS
    lift_factor = 0.5 * rho * BALL_AREA * CL_BASE / BALL_MASS
    elapsed = 0.0
    while elapsed < max_time:
        vx_rel, vy_rel, vz_rel = vx, vy - wind_carry, vz
        relative_speed = math.sqrt(vx_rel**2 + vy_rel**2 + vz_rel**2)
        if relative_speed < 1.0:
            break
        ax = -drag_factor * relative_speed * vx_rel
        ay = -drag_factor * relative_speed * vy_rel
        horizontal_speed = math.sqrt(vx_rel**2 + vy_rel**2)
        az = (
            -drag_factor * relative_speed * vz_rel
            + lift_factor * horizontal_speed * relative_speed * 0.5
            - G
        )
        vx += ax * dt
        vy += ay * dt
        vz += az * dt
        x += vx * dt
        y += vy * dt
        z += vz * dt
        max_height = max(max_height, z)
        elapsed += dt
        if z <= 0 and elapsed > 0.5:
            break
    return round(math.sqrt(x**2 + y**2), 1), round(max_height, 1)


class TrajectoryLookup:
    def __init__(self, path: Path):
        frame = pd.read_parquet(path)
        shape = (
            len(EV_BINS),
            len(LA_BINS),
            len(SPRAY_BINS),
            len(TEMP_BUCKETS),
            len(ALT_BUCKETS),
            len(WIND_BUCKETS),
            2,
        )
        self.values = np.full(shape, np.nan, dtype=np.float32)
        temp_index = {value: index for index, value in enumerate(TEMP_BUCKETS)}
        alt_index = {value: index for index, value in enumerate(ALT_BUCKETS)}
        wind_index = {value: index for index, value in enumerate(WIND_BUCKETS)}
        ev_i = ((frame["ev_mph"].to_numpy(dtype=int) - EV_BINS[0]) // 2).astype(int)
        la_i = ((frame["la_deg"].to_numpy(dtype=int) - LA_BINS[0]) // 2).astype(int)
        spray_i = ((frame["spray_deg"].to_numpy(dtype=int) - SPRAY_BINS[0]) // 5).astype(int)
        temp_i = frame["temp_f"].map(temp_index).to_numpy(dtype=int)
        alt_i = frame["altitude_ft"].map(alt_index).to_numpy(dtype=int)
        wind_i = frame["wind_carry_mph"].map(wind_index).to_numpy(dtype=int)
        self.values[ev_i, la_i, spray_i, temp_i, alt_i, wind_i, 0] = frame[
            "landing_distance_ft"
        ].to_numpy(dtype=np.float32)
        self.values[ev_i, la_i, spray_i, temp_i, alt_i, wind_i, 1] = frame[
            "max_height_ft"
        ].to_numpy(dtype=np.float32)

    def lookup(
        self,
        ev_mph: float,
        la_deg: float,
        spray_deg: float,
        temp_f: float,
        altitude_ft: float,
        wind_carry_mph: float,
    ) -> tuple[float, float]:
        ev = int(np.clip(round(ev_mph / 2) * 2, EV_BINS[0], EV_BINS[-1]))
        launch = int(np.clip(round(la_deg / 2) * 2, LA_BINS[0], LA_BINS[-1]))
        spray = int(np.clip(round(spray_deg / 5) * 5, SPRAY_BINS[0], SPRAY_BINS[-1]))
        temp = min(TEMP_BUCKETS, key=lambda value: abs(value - temp_f))
        altitude = min(ALT_BUCKETS, key=lambda value: abs(value - altitude_ft))
        wind = min(WIND_BUCKETS, key=lambda value: abs(value - wind_carry_mph))
        result = self.values[
            (ev - EV_BINS[0]) // 2,
            (launch - LA_BINS[0]) // 2,
            (spray - SPRAY_BINS[0]) // 5,
            TEMP_BUCKETS.index(temp),
            ALT_BUCKETS.index(altitude),
            WIND_BUCKETS.index(wind),
        ]
        if np.isnan(result).any():
            return simulate_trajectory(
                ev_mph,
                la_deg,
                spray_deg,
                air_density_ratio(temp_f, altitude_ft),
                wind_carry_mph,
            )
        return float(result[0]), float(result[1])


@dataclass
class PhysicsEngine:
    profiles: pd.DataFrame
    geometry: pd.DataFrame
    trajectory: TrajectoryLookup

    @classmethod
    def load(cls, data_dir: Path) -> PhysicsEngine:
        profiles = pd.read_parquet(data_dir / "batter_profiles.parquet")
        geometry = pd.read_parquet(data_dir / "park_geometry.parquet")
        return cls(profiles, geometry, TrajectoryLookup(data_dir / "trajectory_lookup.parquet"))

    def __post_init__(self) -> None:
        self.profile_index = {
            int(row["batter_id"]): row.to_dict()
            for _, row in self.profiles.drop_duplicates("batter_id", keep="last").iterrows()
        }
        self.wall_index: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for team, park in self.geometry.groupby("venue_team", sort=False):
            ordered = park.sort_values("angle_deg")
            self.wall_index[str(team)] = (
                ordered["wall_distance_ft"].to_numpy(dtype=float),
                ordered["wall_height_ft"].to_numpy(dtype=float),
            )

    def wall(self, team: str, spray_angle: float) -> tuple[float, float]:
        values = self.wall_index.get(team)
        if values is None:
            return 400.0, 8.0
        position = max(-45, min(45, round(spray_angle))) + 45
        return float(values[0][position]), float(values[1][position])

    def wall_clearance_rate(
        self,
        profile: dict[str, Any],
        *,
        team: str,
        temp_f: float,
        altitude_ft: float,
        wind_carry_mph: float,
    ) -> float | None:
        ev_values = [profile.get(f"ev_p{value}", 90.0) for value in PROFILE_PERCENTILES]
        launch_values = [profile.get(f"la_p{value}", 15.0) for value in PROFILE_PERCENTILES]
        spray_probabilities = np.array(
            [profile.get(f"spray_bin_{index}", 1 / 9) for index in range(9)], dtype=float
        )
        total = float(np.nansum(spray_probabilities))
        if not np.isfinite(total) or total <= 0:
            return None
        spray_probabilities /= total
        cleared = 0.0
        weight = 0.0
        for ev in ev_values:
            if not np.isfinite(ev):
                continue
            for launch in launch_values:
                if not np.isfinite(launch):
                    continue
                for index, spray in enumerate(SPRAY_BIN_CENTERS):
                    probability = float(spray_probabilities[index])
                    if probability <= 0:
                        continue
                    distance, height = self.trajectory.lookup(
                        float(ev),
                        float(launch),
                        float(spray),
                        temp_f,
                        altitude_ft,
                        wind_carry_mph,
                    )
                    wall_distance, wall_height = self.wall(team, float(spray))
                    if distance >= wall_distance and height >= wall_height and launch > 10:
                        cleared += probability
                    weight += probability
        return cleared / weight if weight else None

    def lineup_index(
        self,
        batter_ids: list[int],
        *,
        venue: Venue,
        temp_f: float,
        wind_carry_mph: float,
    ) -> tuple[float | None, int]:
        weighted = 0.0
        weight_sum = 0.0
        covered = 0
        for slot, batter_id in enumerate(batter_ids[:9]):
            profile = self.profile_index.get(int(batter_id))
            if profile is None:
                continue
            venue_rate = self.wall_clearance_rate(
                profile,
                team=venue.team,
                temp_f=temp_f,
                altitude_ft=venue.altitude_ft,
                wind_carry_mph=wind_carry_mph,
            )
            neutral_rate = self.wall_clearance_rate(
                profile,
                team="NEUTRAL",
                temp_f=67.0,
                altitude_ft=500.0,
                wind_carry_mph=0.0,
            )
            if venue_rate is None or neutral_rate is None or neutral_rate <= 0:
                continue
            pa_weight = PA_WEIGHTS[slot]
            weighted += float(np.clip(venue_rate / neutral_rate, 0.3, 3.0)) * pa_weight
            weight_sum += pa_weight
            covered += 1
        return (round(weighted / weight_sum, 4), covered) if weight_sum else (None, 0)

    def approach_c(
        self,
        lineup: dict[str, Any],
        *,
        venue: Venue,
        weather: dict[str, Any],
    ) -> dict[str, Any]:
        if lineup.get("state") != "confirmed":
            return {
                "state": "not_available",
                "reason": lineup.get("reason") or "both official batting orders are required",
                "used_in_headline": False,
                "method": "neutral-park double ratio",
            }
        home, home_covered = self.lineup_index(
            lineup.get("home_batter_ids") or [],
            venue=venue,
            temp_f=float(weather["temperature_f"]),
            wind_carry_mph=float(weather["wind_carry_mph"]),
        )
        away, away_covered = self.lineup_index(
            lineup.get("away_batter_ids") or [],
            venue=venue,
            temp_f=float(weather["temperature_f"]),
            wind_carry_mph=float(weather["wind_carry_mph"]),
        )
        if home is None or away is None:
            return {
                "state": "not_available",
                "reason": "confirmed lineups did not have enough profiled batters",
                "used_in_headline": False,
                "method": "neutral-park double ratio",
                "home_profile_coverage": home_covered,
                "away_profile_coverage": away_covered,
            }
        return {
            "state": "experimental",
            "reason": (
                "The lineup/trajectory layer is a transparent geometry experiment and is not "
                "used in the headline weather factor."
            ),
            "used_in_headline": False,
            "method": "neutral-park double ratio",
            "home_hr_index": home,
            "away_hr_index": away,
            "home_minus_away": round(home - away, 4),
            "home_profile_coverage": home_covered,
            "away_profile_coverage": away_covered,
        }


def trajectory_theater(venue: Venue, weather: dict[str, Any]) -> dict[str, Any]:
    if weather.get("state") != "verified":
        return {
            "state": "held",
            "reason": "trajectory comparison is hidden without verified game-hour weather",
            "integration": "bounded Euler approximation",
            "arcs": [],
        }
    archetypes = (
        ("pull power", 105.0, 27.0, -25.0),
        ("center carry", 101.0, 29.0, 0.0),
        ("opposite-field gap", 96.0, 24.0, 25.0),
    )
    density = air_density_ratio(float(weather["temperature_f"]), venue.altitude_ft)
    arcs: list[dict[str, Any]] = []
    for name, ev, launch, spray in archetypes:
        weather_distance, weather_height = simulate_trajectory(
            ev,
            launch,
            spray,
            density,
            float(weather["wind_carry_mph"]),
        )
        neutral_distance, neutral_height = simulate_trajectory(
            ev, launch, spray, air_density_ratio(67.0, 500.0), 0.0
        )

        def points(distance: float, height: float) -> list[list[float]]:
            result = []
            for fraction in np.linspace(0.0, 1.0, 9):
                vertical = 3.0 * (1.0 - fraction) + 4.0 * max(height - 3.0, 0.0) * fraction * (
                    1.0 - fraction
                )
                result.append([round(distance * float(fraction), 1), round(max(vertical, 0.0), 1)])
            return result

        arcs.append(
            {
                "archetype": name,
                "exit_velocity_mph": ev,
                "launch_angle_deg": launch,
                "spray_angle_deg": spray,
                "weather_distance_ft": weather_distance,
                "neutral_distance_ft": neutral_distance,
                "carry_delta_ft": round(weather_distance - neutral_distance, 1),
                "weather_points_ft": points(weather_distance, weather_height),
                "neutral_points_ft": points(neutral_distance, neutral_height),
            }
        )
    return {
        "state": "available",
        "reason": None,
        "integration": "bounded Euler approximation",
        "arcs": arcs,
    }

