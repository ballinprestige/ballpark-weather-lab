# Three-Minute Employer Walkthrough

**Repository:** [github.com/ballinprestige/ballpark-weather-lab](https://github.com/ballinprestige/ballpark-weather-lab)

**Live demo:** [ballinprestige.github.io/ballpark-weather-lab](https://ballinprestige.github.io/ballpark-weather-lab/)

The recorded `2026-08-28` recovery release and exact public payload hash are in
[verification.md](verification.md). Daily reliability is deliberately labeled provisional at
`2/7`; this walkthrough does not present the demo as a dependable unattended service yet.

## 0:00–0:20 — Frame the problem

> Ballpark Weather Lab is a focused daily data product. It combines the MLB slate and game-hour
> weather with versioned XGBoost park-factor artifacts, publishes one validated static payload,
> and proves the exact bytes that reached GitHub Pages. The product is intentionally transparent
> about missing evidence.

Point out the release ribbon: slate state, updated timestamp, and short SHA-256. Explain that if
the payload date falls behind the New York calendar date, the ribbon changes to `STALE`, names both
dates, and says not to treat the slate as current even when the underlying payload was `ready`.

## 0:20–1:05 — Follow one game through the Slate

Open **Slate**, point out card/Ledger modes and the whole-slate Wind Field Strip, then open one
matchup's dedicated station and narrate the evidence chain:

1. The headline is a runs park factor around neutral `1.000`, not a score forecast.
2. **Game-hour weather** shows provider, basis, valid time, and fetch time.
3. **Park wind diagram** rotates compass wind into the park's center-field axis.
4. **Decomposition ladder** separates seasonal venue baseline, weather multiplier, and final game
   factor.
5. **Approach C context** is visibly experimental and never changes the headline.
6. **Trajectory theater** compares hypothetical neutral and weather flight arcs for explanation,
   not prediction.

If the selected game has missing weather, use the visible hold to explain that the application
keeps valid neighboring games while withholding that game's modeled weather adjustment.

## 1:05–1:35 — Show operational truth in Data Health

Open **Data Health**.

> This is the operator view embedded in the product: the complete payload hash, generation time,
> artifact manifest receipt, and separate schedule, weather, lineup, and artifact lanes. Optional
> context can be partial without turning a valid park-weather slate into a failure.

Point to the per-game weather and trajectory coverage counts. Explain `ready`, `degraded`, and
`no_slate` as valid, distinguishable publication states.

Also show the daily evidence rule: the product remains `provisional` until seven consecutive
same-day archive hashes and their hosted public-readback receipts have passed.

## 1:35–2:10 — Inspect the model evidence

Open **Method**.

> The headline model is Approach B: two XGBoost regressors for runs and home-run weather
> multipliers. The evidence receipt contains 21,608 total rows—17,075 fit, 2,302 validation, and
> 2,231 held out. The held-out RMSE is 0.5102 for runs multipliers and 0.7173 for home-run
> multipliers. Those are experimental target-unit errors, not outcome-performance claims.

Mention the safeguards: artifact hashes, clipped multipliers, no inference without verified
weather, and JSON Schema plus semantic checks before publication.

## 2:10–2:35 — Show reproducible history

Open **History** and inspect a dated snapshot.

> Each archive entry records date, state, game count, generation time, and payload SHA-256. A hosted
> build restores only canonical dates whose bytes match their recorded hashes. The workflow then
> deploys one Pages artifact and reads the public date and exact payload hash back after deployment.

Return to the current release to show that archive inspection does not blur current freshness.

## 2:35–3:00 — Close on engineering judgment

> The main repair was architectural. A working builder had been coupled to 154 disabled legacy
> tasks and unrelated research dependencies, so the public surface went stale. I isolated the
> schedule-and-weather critical path, made lineup physics optional, added six explicit edge-case
> proofs, sanitized the publication boundary, and used GitHub's Pages identity rather than a
> workstation token. The historical isolated smoke runs completed a 15-game slate with lineups and
> a seven-game slate before lineups; the current repository and live demo have their own separate
> verification receipts. When GitHub then delivered two cron events roughly nine hours late, I
> treated that as a reliability incident: added visible stale-state semantics, staggered recovery
> windows, a late lineup refresh, and a seven-day evidence gate instead of declaring success from
> one recovered run.

Finish in the repository at:

- `README.md` for the product contract.
- `docs/architecture.md` for the dependency graph.
- `MODEL_CARD.md` and `LIMITATIONS.md` for model honesty.
- `docs/security-sanitization-report.md` for the clean-publication threat model.
- `docs/verification.md` for current evidence rather than screenshots of claims.
