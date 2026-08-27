# Model Card: Approach B Weather-Adjusted Park Factors

## Summary

Approach B consists of two XGBoost regression artifacts. One estimates a weather multiplier for
runs park factor and the other estimates a weather multiplier for home-run park factor. At
inference time, each multiplier is applied to a dated seasonal venue baseline.

The output is park context around a neutral value of `1.000`. It is not a score, player, team,
winner, or financial forecast.

**Artifact version:** `2026-04-10`  
**Training receipt:** `assets/models/training_manifest.json`  
**Integrity inventory:** `assets/manifest.json`

## Intended use

- Explain how game-hour weather may move a venue baseline.
- Compare the modeled weather contribution with the seasonal contribution.
- Demonstrate a traceable daily ML publication with explicit degraded states.
- Support engineering review of artifact validation, schema validation, release receipts, and
  public readback.

## Out-of-scope use

- Predicting game winners, final scores, individual events, or player performance.
- Financial, safety-critical, medical, legal, or venue-operating decisions.
- Treating the experimental lineup/trajectory layer as a validated model.
- Inferring causal weather effects from model feature importance.

## Target construction

The historical training implementation formed per-game target multipliers relative to expected
venue-season rates:

- Runs target: total game runs divided by league-average runs times a lagged seasonal runs park
  factor.
- Home-run target: total game home runs divided by league-average home runs times a lagged
  seasonal home-run park factor.

Seasonal baselines used prior-season rolling information with shrinkage toward `1.000`. Raw
training rows are withheld, so the complete training run cannot be independently reproduced from
this repository alone.

## Features

The model receipt lists 13 features:

- Temperature, humidity, wind speed, wind carry, and crosswind.
- Air-density index and surface pressure.
- Venue altitude, roof/dome type, and active-dome indicator.
- Calendar month.
- Temperature × wind-carry and temperature × altitude interactions.

Wind is rotated from compass direction into the venue's approximate center-field axis. Open-air
forecast selection uses the nearest available hour within 90 minutes of scheduled game time.

## Data split

The published receipt contains 21,608 total game rows with a temporal split:

| Split | Rows | Role |
| --- | ---: | --- |
| Fit | 17,075 | XGBoost fitting |
| 2024 validation | 2,302 | Early stopping and model selection |
| 2025 held-out test | 2,231 | One temporal holdout evaluation |

The total must not be described as 21,608 training rows: 4,533 rows were reserved for validation
and test.

## Recorded evaluation

| Target multiplier | Validation RMSE | Best iteration | Held-out RMSE |
| --- | ---: | ---: | ---: |
| Runs | 0.4816 | 28 | 0.5102 |
| Home runs | 0.6833 | 71 | 0.7173 |

RMSE is measured in each noisy, per-game multiplier target's units. The metrics come from one
historical temporal split. There is no published naive baseline, confidence interval,
cross-validation result, calibration study, prospective study, or comparison demonstrating
superiority. These numbers are experimental model-development evidence, not evidence of outcome
or financial performance.

## Inference safeguards

- Required artifacts must match their recorded SHA-256 and size before inference.
- Predicted weather multipliers are clipped to `[0.70, 1.40]`.
- An active fixed-roof condition forces the weather multiplier to `1.000`.
- Unverified weather prevents model inference for that game. The seasonal baseline remains
  visible, the weather delta is zero, and the factor state is `held`.
- The final payload must pass JSON Schema and semantic validation before publication.

Clipping and fallback behavior improve interface stability; they do not validate model accuracy.

## Approach C is separate and experimental

Approach C is not an ensemble member and never changes the headline Approach B factor. When both
official batting orders, verified weather, and optional artifacts are present, it computes a
lineup-specific neutral-park double ratio using:

- 839 derived batter profiles.
- 30 approximate stadium geometries represented by 2,730 wall samples.
- A 3,018,625-row generated trajectory lookup.

Its underlying simulator is a bounded Euler approximation with simplified drag and lift. Profile
coverage can be incomplete, and no predictive validation is published. The payload always marks
this lane `experimental` or `not_available` and sets `used_in_headline` to `false`.

The Trajectory Theater is also illustrative. It compares three hypothetical batted-ball
archetypes in neutral and current weather; it does not predict a particular plate appearance.

## Known risks

- Per-game outcome targets have high variance and can make RMSE difficult to interpret.
- Weather forecasts are not observations at field level and may change after publication.
- Retractable-roof state is not verified by a dedicated roof feed.
- Venue baselines, dimensions, orientations, and player profiles can become stale.
- The temporal split evaluates one past season and may not represent future conditions.
- Withheld training rows limit independent reproducibility and bias analysis.
- Feature-importance gain values do not establish causality.

See [LIMITATIONS.md](LIMITATIONS.md) for product-level limits and [DATA_SOURCES.md](DATA_SOURCES.md)
for provenance and redistribution boundaries.

## Update policy

A model refresh should require a new version, immutable artifact hashes, updated split and metric
receipts, data-provenance review, fixture and contract tests, and explicit comparison against a
simple baseline. A refreshed artifact must not silently replace this version.
