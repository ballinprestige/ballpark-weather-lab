# Data Sources and Artifact Policy

Ballpark Weather Lab separates live public-data adapters, compact derived artifacts, and withheld
row-level source evidence. Inclusion in this repository is not a representation that a source is
public domain or unrestricted.

## Live daily sources

| Source | Use | Stored in a release | Failure behavior | Terms/provenance |
| --- | --- | --- | --- | --- |
| MLB Stats API | Schedule, game identity, teams, game time, probable pitchers | A minimal per-game projection | Required schedule failure stops publication | [Endpoint](https://statsapi.mlb.com/), [MLB terms](https://www.mlb.com/official-information/terms-of-use) |
| MLB game feed | Official batting-order availability | State, observation time, and batter counts; public payload omits batter IDs | Optional; Approach B still publishes | [Endpoint family](https://statsapi.mlb.com/), [MLB terms](https://www.mlb.com/official-information/terms-of-use) |
| Open-Meteo Forecast API | Hourly temperature, humidity, wind, and surface pressure | Selected game-hour values, source, valid time, and fetch time | Per-game neutral hold; seasonal baselines remain visible | [API](https://open-meteo.com/en/docs), [license](https://open-meteo.com/en/license), [terms](https://open-meteo.com/en/terms) |

Open-Meteo data must retain visible attribution to Open-Meteo. The adapter requests the nearest
hour and rejects a forecast more than 90 minutes from scheduled game time. It does not represent
park-level sensor data.

## Published compact artifacts

`assets/manifest.json` is the authoritative inventory. It records byte sizes, SHA-256 digests,
row counts where applicable, criticality, and the source-code evidence commit.

| Artifact family | Published evidence | Provenance and caveat |
| --- | --- | --- |
| Approach B models | Two XGBoost JSON boosters, training receipt, feature-importance receipt | Derived from historical game outcomes, historical weather, lagged venue baselines, and documented features. Row-level training inputs are withheld. |
| Seasonal home-run baselines | Compact 2026 venue table with retrieval time and raw-source digest | Derived from the [Baseball Savant park-factor table](https://baseballsavant.mlb.com/leaderboard/statcast-park-factors). It is a dated input, not an official projection. |
| Venue registry and display geometry | 30 venues; display geometry and 2,730 one-degree wall samples | Project-compiled approximation from publicly observable venue facts. Source-by-source geometry lineage was not preserved; values are not survey-grade, should not be attributed to any named database, and require periodic review. |
| Batter profiles | 839 derived profiles | Aggregated from 2023–2025 batted-ball observations. Raw events are withheld. Player coverage is incomplete and the profiles are used only by experimental Approach C. |
| Trajectory lookup | 3,018,625 generated rows | Generated from the repository's bounded Euler flight approximation across discretized input buckets. It is a computational artifact, not measured tracking data. |

Artifact counts establish inventory and integrity only. They are not accuracy metrics.

## Withheld source evidence

The following are intentionally not redistributed:

- Row-level historical weather used for model construction.
- The joined row-level training dataset.
- Raw batted-ball events and API response archives.
- Provider caches and browser captures.
- Third-party research documents.
- Commercial-feed archives and derived commercial-feed payloads.
- Workstation, runtime, and operational snapshots.

`assets/manifest.json` retains byte counts and SHA-256 receipts for the two withheld training
tables so the evidence boundary is explicit without redistributing their rows.

## Historical model-data lineage

The retained training receipt describes 21,608 game rows:

- 17,075 fit rows from seasons before the 2024 validation season.
- 2,302 validation rows from 2024.
- 2,231 held-out rows from 2025.

Historical game outcomes were assembled from Retrosheet material and joined to historical weather.
Retrosheet retains copyright and requests acknowledgment; see its [data-use notice](https://www.retrosheet.org/game.htm).
Open-Meteo material is subject to its [CC BY 4.0 data license](https://open-meteo.com/en/license)
and service terms. The repository does not contain enough row-level data to reproduce model
training from scratch.

## Rights boundary

Public accessibility is not a data-license grant. The repository's [no-license notice](LICENSE.md)
applies only to original material and cannot grant rights in third-party data, names, marks, or
services. Users must evaluate the current provider terms for their own use. If a published
artifact's provenance or redistribution basis becomes uncertain, remove it from the release until
the issue is resolved; Approach C must degrade without blocking Approach B.

The geometry files contain only the project's approximate numeric display inputs, not copied maps,
photographs, logos, source pages, or a third-party database export. Because the original
source-by-source observation ledger was not retained, the repository makes no source-specific
attribution or completeness claim for those values.
