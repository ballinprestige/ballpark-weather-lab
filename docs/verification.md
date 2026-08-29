# Verification Matrix

This page separates three evidence classes:

1. **Implemented proof:** tests and controls present in this repository.
2. **Historical repair evidence:** isolated live observations made before publication.
3. **Current release proof:** results tied to the public repository, workflow, demo URL,
   date, and payload hash.

All three classes are populated below. Hosted Pages proof is tied to the successful workflow and
an independent byte-for-byte public readback rather than inferred from deployment status.

## Required behavior matrix

| Scenario | Input/trigger | Required result | Implemented evidence |
| --- | --- | --- | --- |
| Normal slate | Valid schedule, weather, and lineups | Schema-valid `ready` payload; Approach B headline; Approach C labeled experimental | `tests/fixtures/normal_slate.json`, `tests/test_pipeline.py`, desktop browser path |
| No slate | Empty MLB schedule | Valid `no_slate` payload with zero games and a clear explanation | `tests/fixtures/no_slate.json`, pipeline/contract tests, browser state test |
| Missing weather | One game has no usable game-hour weather | Valid `degraded` payload; seasonal factors visible; modeled weather headline held | `tests/fixtures/missing_weather.json`, source/model/pipeline tests, browser state test |
| Missing lineup | Weather is valid but official batting orders are absent | Approach B publishes; Approach C is `not_available` and never headline | `tests/fixtures/missing_lineup.json`, source/pipeline tests |
| Malformed optional artifact | Approach C parquet cannot be read or match inventory | Approach B publishes; artifact receipt becomes partial; Approach C disabled | Artifact and pipeline tests |
| Malformed critical artifact | Required model/baseline/display artifact hash or content fails | Stop without overwriting prior valid output | Artifact and pipeline tests |
| Duplicate game ID | Duplicate appears in source or final payload | Reject before publication; browser also rejects duplicate public payload | `tests/fixtures/duplicate_game_ids.json`, source/contract/pipeline/browser tests |
| Stale public date | Valid prior payload remains live after the New York date advances | Replace normal release state with an accessible `STALE` warning that names both dates and says not to treat the slate as current | Freshness unit tests and desktop browser state test |
| Release hash mismatch | Release pointer and downloaded payload bytes disagree | Fail closed before any slate values render | Desktop browser readback test |
| Full 15-game slate | Complete daily slate | Three-column desktop card grid, Ledger path, and no root overflow | Deterministic 15-game browser layout test |

Additional implemented controls cover cross-date games, malformed timestamps, weather receipts
attached to the wrong game, stable canonical JSON, atomic replacement failure, sorted/deduplicated
history, bounded public readback, and hash-checked history restoration.

## Desktop and mobile employer paths

The Playwright suite defines:

- A desktop path through the card-first Slate, Ledger mode, dedicated game station, park wind
  diagram, decomposition ladder, trajectory theater, payload SHA-256, Data Health, Method, and
  History.
- A mobile path that opens a dedicated station at the top, restores the originating card's exact
  scroll/focus position on return, checks for horizontal overflow, and requires primary controls
  to be at least 44 × 44 CSS pixels.
- Desktop error/empty paths for no slate, missing weather, malformed public payload, and duplicate
  game IDs, plus an exact release-hash mismatch failure.

The current local Playwright receipt is ten passed and ten intentionally skipped: eight
desktop-scoped checks and two mobile-scoped checks passed, while each inverse project was skipped
by design. The public-URL receipt remains a separate post-deployment gate.

## Historical repair evidence

| Date | Observation | Interpretation | Does not prove |
| --- | --- | --- | --- |
| 2026-08-26 | An isolated live smoke run completed all 15 scheduled games with confirmed lineups; external publication was disabled. | The standalone builder could complete a normal full slate with the optional lineup path available. | This repository's current tests, hosted workflow, Pages deployment, or public hash. |
| 2026-08-27 | An isolated live smoke run completed all seven scheduled games with valid weather before official lineups were available. | The core park-weather slate remained usable while the optional lineup path reported honest unavailability. | This repository's current tests, hosted workflow, Pages deployment, or public hash. |

These are incident-repair observations, not model-quality results.

## Current clean-repository verification

Local receipts below were refreshed from the sanitized release candidate on 2026-08-28. Hosted
release values link to the public repository, Pages workflow, and public readback.

| Check | Status | Evidence |
| --- | --- | --- |
| Python tests | PASS (local + hosted) | 70 tests passed locally and in the reliability-repair workflow; Ruff reported no findings |
| Artifact inventory verification | PASS (local + hosted) | 9 files; manifest `c0b501f9290d2e80f041a0dc689e61036b4b5acec9df60e4ff88949ce3027b78`; 21,608 evidence games; 839 profiles; 3,018,625 trajectory rows; 30 stadiums |
| Svelte type/accessibility checks | PASS (local + hosted) | `svelte-check` reported 0 errors and 0 warnings |
| Frontend unit tests | PASS (local + hosted) | 11 Vitest tests passed locally and in the reliability-repair workflow |
| Production frontend build and budget | PASS (local; hosted gate pending) | Current Vite build: 49.8 KiB initial JS + CSS gzip against 112 KiB; 67.5 KiB self-hosted WOFF2 fonts against 75 KiB, with legacy WOFF rejected |
| Desktop Playwright path | PASS (local; hosted gate pending) | 8 desktop-scoped checks passed, including full evidence, 15-game grid/Ledger, stale, degraded/error, and hash-mismatch states |
| Mobile Playwright path | PASS (local; hosted gate pending) | 2 mobile checks passed: dedicated-station scroll/focus restoration, no root overflow, 44 × 44 primary targets, and a visible one-game Wind Field |
| Secret/private-identifier scan of repository | PASS (local candidate) | High-confidence secret, private-key, account, email, absolute-path, and retired-scope signatures returned no unresolved finding |
| Secret/private-identifier scan of `web/dist` | PASS (local build) | The same binary-aware signatures returned no finding in the production build |
| Dependency vulnerability/license review | PASS (local) | `pip-audit` and `npm audit --audit-level=low` found no known vulnerabilities; dependency licenses reviewed against both locked inventories |
| GitHub Pages reliability-repair deployment | PASS | [Workflow run 33223744721](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33223744721) passed the complete code, artifact, desktop/mobile, OIDC Pages, public readback, and rolling-evidence gates |
| Public reliability-repair date/hash readback | PASS | `2026-08-28`; 15 games; `f96bf07b8f033227a4226519830cd391760e1782a59328c7fe79b59299b33f62`; verified by the deployment job at `2026-08-29T00:33:06Z` |

### Seven-day archive gate and daily-reliability claim

The live app is **not yet proven reliable daily**. `verify-reliability` currently reports `2/7`
consecutive same-day, contract-valid, hash-matched archive-generation receipts ending August 28:

| Slate | Games | Generated on New York slate date | Payload SHA-256 | Hosted readback |
| --- | ---: | --- | --- | --- |
| `2026-08-28` | 15 | PASS | `f96bf07b8f033227a4226519830cd391760e1782a59328c7fe79b59299b33f62` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33223744721) |
| `2026-08-27` | 7 | PASS | `4b414e95b3b09093dd84e5c042175000cc77e44de72d1dc8958cc1c14234be0a` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33129685641) |

Both releases contained verified weather, confirmed lineups, optional Approach C context, and
trajectories for every game. The two original cron events were delivered roughly nine hours late;
the August 28 archive was then refreshed by the reliability-repair deployment above. These
receipts prove successful same-day publication and repair, not dependable schedule delivery. The
claim remains provisional until seven consecutive dates pass and retain their matching
workflow/public-readback receipts.

A non-publishing live-source run from the clean repository at `2026-08-27T11:27:07Z` returned all
seven scheduled games, verified game-hour weather for all seven, reported official lineups as not
yet available, kept Approach C out of the headline, and produced local payload SHA-256
`75d7504248847cc3f753e99cbacafa102b56e06219d4b0c0c6905a914af0ab5c`.

### August 28 reliability-repair receipt

- Public repository URL: <https://github.com/ballinprestige/ballpark-weather-lab>
- Live demo URL: <https://ballinprestige.github.io/ballpark-weather-lab/>
- Workflow run URL: <https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33223744721>
- Verified slate date: `2026-08-28`
- Verified payload SHA-256: `f96bf07b8f033227a4226519830cd391760e1782a59328c7fe79b59299b33f62`
- Verification timestamp: `2026-08-29T00:33:06Z`

## Reproduction commands

```bash
python -m pytest
python -m ballpark verify-artifacts
python -m ballpark daily --date 2026-08-26 --fixture tests/fixtures/normal_slate.json \
  --generated-at 2026-08-26T12:00:00Z
npm run verify --prefix web
npm run test:e2e --prefix web
```

Verify the recorded public release directly:

```bash
python -m ballpark verify-public \
  --url "https://ballinprestige.github.io/ballpark-weather-lab/" \
  --expected-date "2026-08-28" \
  --expected-sha "f96bf07b8f033227a4226519830cd391760e1782a59328c7fe79b59299b33f62"

python -m ballpark verify-reliability \
  --url "https://ballinprestige.github.io/ballpark-weather-lab/" \
  --ending-date 2026-08-28
```
