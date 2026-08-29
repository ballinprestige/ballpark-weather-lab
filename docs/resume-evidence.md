# Resume-Ready Evidence

The bullets below are backed by linked code, tests, hosted workflow output, and the public receipt
recorded in [verification.md](verification.md).

## Concise bullets

- Built a standalone Python/Svelte park-weather data product that turns MLB schedule and
  game-hour weather into schema-validated park factors, atomic release files, and a static GitHub
  Pages deployment with exact public date/SHA-256 readback.
- Designed explicit degraded behavior for normal slate, no slate, missing weather, missing lineup,
  malformed artifact, and duplicate-ID cases; optional lineup physics never blocks the core
  publication.
- Packaged two XGBoost weather-multiplier artifacts with a transparent 21,608-row temporal receipt
  (17,075 fit / 2,302 validation / 2,231 held out), model card, provenance boundary, and limitations
  instead of unsupported performance claims.
- Converted an overcoupled daily workflow involving 154 disabled legacy tasks into one daily CLI
  command and one least-privilege Pages workflow with bounded networking, concurrency protection,
  staggered publication opportunities, artifact validation, and post-deploy readback.
- Added an Eastern-time stale-release alarm and a hash-validating seven-day evidence gate so the
  interface and project documentation cannot present a delayed `READY` payload as current or
  promote a recovered run into a reliability claim.
- Audited an 834-file, approximately 166 MB source tree and designed a clean-history allowlisted
  publication boundary that excluded approximately 135 MB of unlicensed research, private runtime
  metadata, commercial-feed archives, raw source data, and credential machinery.
- Built desktop and mobile employer-demo paths covering slate inspection, wind geometry, factor
  decomposition, Data Health, model evidence, hash-checked history, responsive touch targets, and
  fail-closed malformed-payload states.

## Short project description

Ballpark Weather Lab is an evidence-first MLB park-factor application. A bounded daily Python
pipeline validates model artifacts, fetches the slate and game-hour weather, produces honest
per-game ready/hold states, validates one JSON contract, and builds a responsive Svelte interface.
GitHub Pages deployment uses OIDC and verifies the public payload's exact date and SHA-256.

## STAR interview outline

**Situation:** A polished public surface was stale even though isolated builder runs succeeded.
The publication wrapper depended on 154 disabled legacy tasks and unrelated optional research
lanes.

**Task:** Recover the useful park-weather product without changing the older production system,
publishing private history, or turning missing optional data into a false outage.

**Action:** Reduced the graph to schedule + weather → Approach B → optional Approach C → one
validated payload → one static build → Pages → public readback. Added artifact manifests,
schema/semantic validation, deterministic edge-case fixtures, atomic file replacement, Data
Health, model/rights documentation, clean-history sanitization, and desktop/mobile tests.

**Result:** Historical isolated live smoke runs completed all 15 games on August 26, 2026 with
confirmed lineups and all seven games on August 27 with valid weather before lineups. The public
repository then produced hash-verified same-day releases for August 27 and August 28, including
verified weather, confirmed lineups, Approach C context, and trajectories for every game. The
rolling reliability gate remains honestly provisional at `2/7` because GitHub delivered both
scheduled events roughly nine hours late. See [verification.md](verification.md) for the workflow
and SHA-256 receipts.

## Evidence map

| Claim | Repository evidence |
| --- | --- |
| One-command daily pipeline | `README.md`, `src/ballpark/cli.py`, `docs/operations.md` |
| Required vs optional dependency design | `src/ballpark/pipeline.py`, `docs/architecture.md` |
| Schema and semantic validation | `schemas/slate.schema.json`, `src/ballpark/contract.py`, contract tests |
| Artifact integrity | `assets/manifest.json`, `src/ballpark/artifacts.py`, artifact tests |
| Model counts and RMSE | `assets/models/training_manifest.json`, `MODEL_CARD.md` |
| Public byte verification | `src/ballpark/publication.py`, `.github/workflows/pages.yml`, publication tests |
| Stale-state and seven-day reliability gate | `web/src/lib/freshness.ts`, `src/ballpark/reliability.py`, focused unit/browser tests |
| Desktop/mobile interaction | `web/tests/e2e/app.spec.ts` and the hosted Playwright receipt in `docs/verification.md` |
| Sanitization and rights boundary | `docs/security-sanitization-report.md`, `DATA_SOURCES.md`, `THIRD_PARTY_NOTICES.md` |
| Historical repair evidence | `docs/incident-repair-case-study.md`, `docs/verification.md` |

## Evidence rule

Current repository, demo, hosted workflow, browser matrix, and public-hash claims must stay tied to
the durable receipt in [verification.md](verification.md). Historical isolated smoke runs remain
clearly labeled as incident evidence rather than current hosted proof.
