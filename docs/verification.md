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
| Full 15-game slate | Complete daily slate | Desktop table by default, optional three-column card view, and no root overflow | Deterministic 15-game browser layout test |

Additional implemented controls cover cross-date games, malformed timestamps, weather receipts
attached to the wrong game, stable canonical JSON, atomic replacement failure, sorted/deduplicated
history, bounded public readback, and hash-checked history restoration.

## Desktop and mobile employer paths

The Playwright suite defines:

- A desktop path through the table-first slate, optional card view, dedicated game detail, park wind
  diagram, decomposition ladder, trajectory theater, payload SHA-256, Data Health, Method, and
  History.
- A mobile path that opens dedicated game details at the top, restores the originating card's exact
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
| Python tests | PASS (local + hosted) | 70 tests passed locally and in the neutral-interface workflow; Ruff reported no findings |
| Artifact inventory verification | PASS (local + hosted) | 9 files; manifest `c0b501f9290d2e80f041a0dc689e61036b4b5acec9df60e4ff88949ce3027b78`; 21,608 evidence games; 839 profiles; 3,018,625 trajectory rows; 30 stadiums |
| Svelte type/accessibility checks | PASS (local + hosted) | `svelte-check` reported 0 errors and 0 warnings |
| Frontend unit tests | PASS (local + hosted) | 11 Vitest tests passed locally and in the neutral-interface workflow |
| Production frontend build and budget | PASS (local + hosted) | Current Vite build: 48.5 KiB initial JS + CSS gzip against 112 KiB; no custom font assets are bundled |
| Desktop Playwright path | PASS (local + hosted) | 8 desktop-scoped checks cover full evidence, 15-game table/card layouts, stale, degraded/error, and hash-mismatch states |
| Mobile Playwright path | PASS (local + hosted) | 2 mobile checks cover detail scroll/focus restoration, no root overflow, 44 × 44 primary targets, and a visible one-game wind comparison |
| Secret/private-identifier scan of repository | PASS (local candidate) | High-confidence secret, private-key, account, email, absolute-path, and retired-scope signatures returned no unresolved finding |
| Secret/private-identifier scan of `web/dist` | PASS (local build) | The same binary-aware signatures returned no finding in the production build |
| Dependency vulnerability/license review | PASS (local) | `pip-audit` and `npm audit --audit-level=low` found no known vulnerabilities; dependency licenses reviewed against both locked inventories |
| Neutral-interface GitHub Pages deployment | PASS | [Workflow run 33230839442](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33230839442) passed the complete code, artifact, desktop/mobile, OIDC Pages, public readback, and rolling-evidence gates |
| Neutral-interface public date/hash readback | PASS | `2026-08-28`; 15 games; `9d2a1ed389e38e9ab188d0f2e64ec71d265131da2ef3e094de898fecddbc76d3`; verified byte-for-byte by the deployment job at `2026-08-29T03:15:12Z` |
| Live Google Chrome desktop/mobile inspection | PASS | System UI font resolved for body and headings; no custom font faces loaded; neutral `#f5f7fa` canvas and `#1769aa` interaction token; no document overflow; 15 compact mobile cards and the full game-detail evidence path rendered |

### Seven-day archive gate and daily-reliability claim

The live app met its defined seven-day reliability gate. At `2026-09-03T00:17:32Z`, an independent
cache-busted readback and `verify-reliability --ending-date 2026-09-02` reported `7/7`. Every final
archive payload is contract-valid, was generated on its `America/New_York` slate date, matches the
listed SHA-256 byte-for-byte, and appears in a successful hosted run whose deployment job recorded
the same date, hash, and `verified` public-readback state:

| Slate | Games | Generated at (UTC) | Payload SHA-256 | Hosted exact readback |
| --- | ---: | --- | --- | --- |
| `2026-09-02` | 15 | `2026-09-02T21:59:46.291676Z` | `e5337af2e4567890597b537f4c4a9614270beef876457429f2d6b11c0d0fe065` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33687993867) |
| `2026-09-01` | 15 | `2026-09-02T01:21:07.916357Z` | `4b3b2153cea93152a49d5411bc7989ae6829cd69426d3cc3bb1db8907ff6d52b` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33578931894) |
| `2026-08-31` | 12 | `2026-09-01T02:02:08.767263Z` | `daa4e68ff0c8b06e0d545a7cb4262a9196358fef6516f4ecc54960c198cea99d` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33460936679) |
| `2026-08-30` | 14 | `2026-08-31T01:35:11.912219Z` | `b57a6b741237f4eb8d90655c06fcf9347f2d065d9508ca3c16606eb1da26ebee` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33347930619) |
| `2026-08-29` | 17 | `2026-08-30T01:37:16.440582Z` | `b7ce170dd1f07e3d6b548c62cecb5f8edfdb1eeb18466ad8f0fbcbd394f7564b` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33286045368) |
| `2026-08-28` | 15 | `2026-08-29T03:14:25.567252Z` | `9d2a1ed389e38e9ab188d0f2e64ec71d265131da2ef3e094de898fecddbc76d3` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33230839442) |
| `2026-08-27` | 7 | `2026-08-28T00:26:55.112901Z` | `4b414e95b3b09093dd84e5c042175000cc77e44de72d1dc8958cc1c14234be0a` | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33129685641) |

The first two original cron events were delivered roughly nine hours late, which prompted the
staggered schedule windows and explicit stale-state warning. The completed window proves seven
consecutive same-day hosted publications under the repository's acceptance rule. It does not make
GitHub schedule delivery a guaranteed service or remove the need for public freshness checks.

A non-publishing live-source run from the clean repository at `2026-08-27T11:27:07Z` returned all
seven scheduled games, verified game-hour weather for all seven, reported official lineups as not
yet available, kept Approach C out of the headline, and produced local payload SHA-256
`75d7504248847cc3f753e99cbacafa102b56e06219d4b0c0c6905a914af0ab5c`.

### August 28 neutral-interface receipt

- Public repository URL: <https://github.com/ballinprestige/ballpark-weather-lab>
- Live demo URL: <https://ballinprestige.github.io/ballpark-weather-lab/>
- Workflow run URL: <https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33230839442>
- Verified slate date: `2026-08-28`
- Verified payload SHA-256: `9d2a1ed389e38e9ab188d0f2e64ec71d265131da2ef3e094de898fecddbc76d3`
- Verification timestamp: `2026-08-29T03:15:12Z`

## Reproduction commands

```bash
python -m pytest
python -m ballpark verify-artifacts
python -m ballpark daily --date 2026-08-26 --fixture tests/fixtures/normal_slate.json \
  --generated-at 2026-08-26T12:00:00Z
npm run verify --prefix web
npm run test:e2e --prefix web
```

Verify the latest release and completed seven-day ledger directly:

```bash
python -m ballpark verify-public \
  --url "https://ballinprestige.github.io/ballpark-weather-lab/" \
  --expected-date "2026-09-02" \
  --expected-sha "e5337af2e4567890597b537f4c4a9614270beef876457429f2d6b11c0d0fe065"

python -m ballpark verify-reliability \
  --url "https://ballinprestige.github.io/ballpark-weather-lab/" \
  --ending-date 2026-09-02
```
