# Verification Matrix

This page separates three evidence classes:

1. **Implemented proof:** tests and controls present in this repository.
2. **Historical repair evidence:** isolated live observations made before publication.
3. **Current release proof:** results tied to the eventual public repository, workflow, demo URL,
   date, and payload hash.

The first two classes and the clean local release gates are populated below. Hosted Pages proof
remains intentionally pending until it is read back from the public URL.

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

Additional implemented controls cover cross-date games, malformed timestamps, weather receipts
attached to the wrong game, stable canonical JSON, atomic replacement failure, sorted/deduplicated
history, bounded public readback, and hash-checked history restoration.

## Desktop and mobile employer paths

The Playwright suite defines:

- A desktop path through Slate, matchup expansion, park wind diagram, decomposition ladder,
  trajectory theater, payload SHA-256, Data Health, Method, and History.
- A mobile path that switches expanded matchups, checks for horizontal overflow, and requires all
  visible buttons and navigation links to be at least 44 × 44 CSS pixels.
- Desktop error/empty paths for no slate, missing weather, malformed public payload, and duplicate
  game IDs.

The local Playwright receipt on 2026-08-27 was six passed and six intentionally skipped: five
desktop-scoped checks and one mobile-scoped check passed, while each inverse project was skipped by
design. Hosted workflow and public-URL receipts remain separate gates.

## Historical repair evidence

| Date | Observation | Interpretation | Does not prove |
| --- | --- | --- | --- |
| 2026-08-26 | An isolated live smoke run completed all 15 scheduled games with confirmed lineups; external publication was disabled. | The standalone builder could complete a normal full slate with the optional lineup path available. | This repository's current tests, hosted workflow, Pages deployment, or public hash. |
| 2026-08-27 | An isolated live smoke run completed all seven scheduled games with valid weather before official lineups were available. | The core park-weather slate remained usable while the optional lineup path reported honest unavailability. | This repository's current tests, hosted workflow, Pages deployment, or public hash. |

These are incident-repair observations, not model-quality results.

## Current clean-repository verification

Local receipts below were recorded from the sanitized release candidate on 2026-08-27. Replace
only the hosted values with durable evidence from the final public repository and demo.

| Check | Status | Evidence |
| --- | --- | --- |
| Python tests | PASS (local) | 51 tests passed; Ruff reported no findings |
| Artifact inventory verification | PASS (local + hosted) | 9 files; manifest `bbf71642ab0e1ab4ecc2283f9d93ce646b064bebd50618130433787ff224c720`; 21,608 evidence games; 839 profiles; 3,018,625 trajectory rows; 30 stadiums |
| Svelte type/accessibility checks | PASS (local) | `svelte-check` reported 0 errors and 0 warnings |
| Frontend unit tests | PASS (local) | 6 Vitest tests passed |
| Production frontend build and budget | PASS (local) | Vite build passed; initial JS + CSS measured 39.2 KiB gzip against a 112 KiB budget |
| Desktop Playwright path | PASS (local) | 5 desktop-scoped checks passed, including the complete employer path and four degraded/error states |
| Mobile Playwright path | PASS (local) | Compact/expandable slate, no horizontal overflow, and 44 × 44 CSS-pixel targets passed |
| Secret/private-identifier scan of repository | PASS (local candidate) | High-confidence secret, private-key, account, email, absolute-path, and retired-scope signatures returned no unresolved finding |
| Secret/private-identifier scan of `web/dist` | PASS (local build) | The same binary-aware signatures returned no finding in the production build |
| Dependency vulnerability/license review | PASS (local) | `pip-audit` and `npm audit --audit-level=low` found no known vulnerabilities; dependency licenses reviewed against both locked inventories |
| GitHub Pages deployment | PENDING | Workflow run URL pending |
| Public date/hash readback | PENDING | Demo URL, date, and SHA-256 pending |

A non-publishing live-source run from the clean repository at `2026-08-27T11:27:07Z` returned all
seven scheduled games, verified game-hour weather for all seven, reported official lineups as not
yet available, kept Approach C out of the headline, and produced local payload SHA-256
`75d7504248847cc3f753e99cbacafa102b56e06219d4b0c0c6905a914af0ab5c`.

### Publication placeholders

- Public repository URL: **PENDING PUBLICATION**
- Live demo URL: **PENDING PUBLICATION**
- Workflow run URL: **PENDING PUBLICATION**
- Verified slate date: **PENDING PUBLICATION**
- Verified payload SHA-256: **PENDING PUBLICATION**
- Verification timestamp: **PENDING PUBLICATION**

## Reproduction commands

```bash
python -m pytest
python -m ballpark verify-artifacts
python -m ballpark daily --date 2026-08-26 --fixture tests/fixtures/normal_slate.json \
  --generated-at 2026-08-26T12:00:00Z
npm run verify --prefix web
npm run test:e2e --prefix web
```

After deployment:

```bash
python -m ballpark verify-public --url "PUBLIC_DEMO_URL" --receipt web/dist/data/release.json
```
