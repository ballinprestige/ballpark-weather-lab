from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Venue:
    team: str
    team_id: int
    name: str
    latitude: float
    longitude: float
    altitude_ft: int
    dome_type: int
    seasonal_pf_runs: float
    center_field_azimuth: float

    @property
    def roof_label(self) -> str:
        return {0: "open-air", 1: "retractable", 2: "fixed-roof"}[self.dome_type]


def _v(
    team: str,
    team_id: int,
    name: str,
    latitude: float,
    longitude: float,
    altitude_ft: int,
    dome_type: int,
    seasonal_pf_runs: float,
    center_field_azimuth: float,
) -> Venue:
    return Venue(
        team,
        team_id,
        name,
        latitude,
        longitude,
        altitude_ft,
        dome_type,
        seasonal_pf_runs,
        center_field_azimuth,
    )


# Public venue coordinates, dimensions metadata, and 2025 seasonal run baselines.
VENUES: dict[str, Venue] = {
    "ARI": _v("ARI", 109, "Chase Field", 33.4453, -112.0667, 1106, 1, 1.049, 270),
    "ATL": _v("ATL", 144, "Truist Park", 33.8907, -84.4677, 1050, 0, 1.014, 50),
    "BAL": _v("BAL", 110, "Oriole Park at Camden Yards", 39.2839, -76.6216, 30, 0, 1.020, 30),
    "BOS": _v("BOS", 111, "Fenway Park", 42.3467, -71.0972, 20, 0, 1.030, 35),
    "CHC": _v("CHC", 112, "Wrigley Field", 41.9484, -87.6553, 600, 0, 1.050, 65),
    "CWS": _v("CWS", 145, "Rate Field", 41.8299, -87.6338, 595, 0, 1.010, 335),
    "CIN": _v("CIN", 113, "Great American Ball Park", 39.0974, -84.5065, 500, 0, 1.080, 315),
    "CLE": _v("CLE", 114, "Progressive Field", 41.4962, -81.6852, 660, 0, 0.970, 335),
    "COL": _v("COL", 115, "Coors Field", 39.7559, -104.9942, 5183, 0, 1.261, 355),
    "DET": _v("DET", 116, "Comerica Park", 42.3390, -83.0485, 600, 0, 0.960, 130),
    "HOU": _v("HOU", 117, "Daikin Park", 29.7573, -95.3555, 40, 1, 1.040, 65),
    "KC": _v("KC", 118, "Kauffman Stadium", 39.0517, -94.4803, 820, 0, 0.990, 45),
    "LAA": _v("LAA", 108, "Angel Stadium", 33.8003, -117.8827, 160, 0, 0.980, 45),
    "LAD": _v("LAD", 119, "Dodger Stadium", 34.0739, -118.2400, 515, 0, 0.970, 325),
    "MIA": _v("MIA", 146, "loanDepot park", 25.7781, -80.2196, 7, 1, 0.930, 350),
    "MIL": _v("MIL", 158, "American Family Field", 43.0280, -87.9712, 640, 1, 1.020, 15),
    "MIN": _v("MIN", 142, "Target Field", 44.9817, -93.2776, 840, 0, 1.010, 5),
    "NYM": _v("NYM", 121, "Citi Field", 40.7571, -73.8458, 20, 0, 0.940, 40),
    "NYY": _v("NYY", 147, "Yankee Stadium", 40.8296, -73.9262, 20, 0, 1.050, 35),
    "OAK": _v("OAK", 133, "Sutter Health Park", 38.5802, -121.5071, 30, 0, 0.980, 355),
    "PHI": _v("PHI", 143, "Citizens Bank Park", 39.9061, -75.1665, 25, 0, 1.060, 25),
    "PIT": _v("PIT", 134, "PNC Park", 40.4469, -80.0057, 730, 0, 0.950, 25),
    "SD": _v("SD", 135, "Petco Park", 32.7073, -117.1566, 20, 0, 0.920, 330),
    "SF": _v("SF", 137, "Oracle Park", 37.7786, -122.3893, 10, 0, 0.930, 35),
    "SEA": _v("SEA", 136, "T-Mobile Park", 47.5914, -122.3325, 20, 1, 0.940, 355),
    "STL": _v("STL", 138, "Busch Stadium", 38.6226, -90.1928, 455, 0, 0.970, 45),
    "TB": _v("TB", 139, "Tropicana Field", 27.7682, -82.6534, 45, 2, 0.910, 0),
    "TEX": _v("TEX", 140, "Globe Life Field", 32.7512, -97.0832, 545, 1, 1.010, 340),
    "TOR": _v("TOR", 141, "Rogers Centre", 43.6414, -79.3894, 270, 1, 1.000, 5),
    "WSH": _v("WSH", 120, "Nationals Park", 38.8730, -77.0074, 25, 0, 0.990, 30),
}

TEAM_BY_ID = {venue.team_id: venue.team for venue in VENUES.values()}
