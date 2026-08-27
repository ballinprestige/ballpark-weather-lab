# Limitations

Ballpark Weather Lab is an engineering and model-transparency portfolio project. Its most useful
property is that it shows what it knows, what it is holding, and why.

## Interpretation

- Park factors describe venue and environmental context around `1.000`; they are not game scores,
  event probabilities, team ratings, or outcome predictions.
- The displayed runs and home-run factors are separate model outputs and should not be combined
  into an implied forecast.
- Historical RMSE values describe one experiment in noisy multiplier units. They do not establish
  practical value, superiority, or future performance.
- Feature importance is descriptive of fitted trees, not causal evidence.

## Weather

- Open-Meteo is a forecast service, not an in-park sensor network.
- The adapter selects the nearest hourly value within 90 minutes of scheduled first pitch.
- Forecasts can change after publication, games can move, and delays can make the selected hour
  stale.
- Wind measured at standard forecast height does not capture stadium bowl effects, open panels,
  nearby structures, or field-level gusts.
- Retractable-roof state is reported as unconfirmed; fixed-roof conditions use standard indoor
  assumptions rather than a live venue sensor.
- When weather cannot be verified, the project holds the weather-adjusted headline and displays
  dated seasonal baselines. Neutral fallback values are operational sentinels, not observations.

## Model and data

- Training uses historical game outcomes and weather joins; row-level source data is withheld for
  redistribution reasons, so full retraining is not reproducible from this repository.
- Only one temporal validation year and one temporal test year are reported.
- No naive baseline, uncertainty interval, calibration curve, cross-validation result, or
  prospective test is published.
- Venue baselines and the 2026 home-run baseline are dated and can drift.
- The venue registry contains approximate coordinates, orientations, dimensions, and roof
  classifications. It is not survey-grade.
- The model does not encode every possible game condition and intentionally omits many team- and
  player-level variables.
- Model multipliers are clipped between `0.70` and `1.40`, which is an engineering guardrail rather
  than an empirical confidence bound.

## Experimental lineup and trajectory context

- Approach C is optional, experimental, and never used in the headline factor.
- Batter profiles cover 839 players from a dated historical window; missing and changing players
  reduce coverage.
- Stadium wall geometry is approximate.
- The trajectory lookup uses discretized buckets and a bounded Euler approximation, not a full
  aerodynamic or tracking model.
- Lineup context requires both official nine-player batting orders and verified weather. Partial,
  pending, or unavailable lineups produce a visible `not_available` state.
- Trajectory Theater uses hypothetical batted-ball archetypes and should not be read as a
  player-specific prediction.

## Publication and history

- A required schedule failure stops the run; the prior public release may therefore remain current
  until a later successful build.
- Atomic replacement protects each local JSON file, while GitHub Pages promotes the built site as
  one deployment. The local multi-file staging directory is not a transactional database.
- Public SHA-256 readback proves byte equality with the release receipt, not truth of the source
  data or authenticity against a compromised maintainer.
- History is restored from the prior public site only when dates are canonical and hashes match.
  It is not an independent backup.
- GitHub Actions scheduling is best effort and can be delayed or disabled by platform or repository
  state.

## Rights and affiliation

- Third-party data and names remain governed by provider terms; public availability does not imply
  unrestricted reuse.
- The project is independent and not endorsed by MLB, its clubs, data providers, or venue
  operators.
- Review [DATA_SOURCES.md](DATA_SOURCES.md), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), and
  [LICENSE.md](LICENSE.md) before relying on or reusing any material.
