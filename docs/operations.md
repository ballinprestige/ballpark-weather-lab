# Operations

## Daily objective

Produce one schema-valid, self-describing static release for the selected MLB slate, deploy one
GitHub Pages artifact, and prove that the public date and payload bytes match the local receipt.

## One-time setup

Install Python 3.12 or newer and Node.js 22.x, then run from the repository root:

```bash
python -m venv .venv
python -m pip install --require-hashes --requirement requirements.lock
python -m pip install --no-build-isolation --no-deps --editable .
npm ci --prefix web
```

Activate `.venv` using the normal command for the operating system.

## One daily command

```bash
python -m ballpark daily
```

The default date is the current `America/New_York` calendar date. For an explicit date:

```bash
python -m ballpark daily --date 2026-08-26
```

For a deterministic, network-free demonstration:

```bash
python -m ballpark daily --date 2026-08-26 --fixture tests/fixtures/normal_slate.json \
  --generated-at 2026-08-26T12:00:00Z
```

Successful operation leaves the complete static build in `web/dist`. The command itself checks
that the distribution's payload bytes match `web/dist/data/release.json`.

## Preflight verification

```bash
python -m pytest
python -m ballpark verify-artifacts
npm run verify --prefix web
npm run test:e2e --prefix web
```

The browser suite requires Playwright's supported browser binaries. Install those through the
Playwright CLI when the local environment does not already have them.

Do not treat a successful fixture build as a live-source or public deployment test. Record each
evidence class separately in [verification.md](verification.md).

## GitHub Pages setup

1. Publish this project as a new repository with fresh history.
2. In repository settings, select **GitHub Actions** as the Pages source.
3. Keep Actions enabled and retain the workflow's least-privilege permissions.
4. Do not create a personal access token for Pages.
5. Run the workflow manually once before relying on the schedule.
6. Record the repository, demo, workflow, date, and hash receipt only after public readback
   succeeds.

`.github/workflows/pages.yml` has three staggered daily schedule opportunities, also runs on
pushes to `main`, and retains manual dispatch with an optional canonical `YYYY-MM-DD` date:

| UTC | Purpose |
| --- | --- |
| `15:17` | Morning baseline publication |
| `19:43` | Midday recovery if the baseline event was delayed or dropped |
| `23:37` | Late refresh for official lineups and optional Approach C/trajectory context |

All three scheduled runs resolve the slate from the current `America/New_York` date when they
actually execute. The deliberately irregular minutes avoid the start-of-hour load window. GitHub
documents that scheduled events can be delayed and, under sufficient load, dropped; these
additional opportunities reduce that risk but do not turn cron delivery into proof of freshness.
They remain in the same GitHub scheduler failure domain; only a successful public date/hash
readback proves a publication. See GitHub's
[scheduled-workflow guidance](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The workflow:

1. Installs the locked direct dependencies.
2. Runs Python tests, artifact verification, Svelte checks, and frontend unit tests.
3. Restores valid public history when available.
4. Runs `python -m ballpark daily`.
5. Uploads `web/dist` as one Pages artifact.
6. Deploys with GitHub's Pages identity.
7. Reads back the public date and exact payload SHA-256 with bounded retries.
8. Reports the seven-day archive-generation evidence gate without promoting a provisional
   streak into a reliability claim.

Concurrency uses one `pages` group and does not cancel an in-progress deployment.

Each run writes its event type, schedule expression, and UTC runner-acceptance time to the GitHub
Actions job summary. That receipt distinguishes scheduler-delivery latency from build or deploy
latency without adding a credential or external service.

### Scheduled-delivery incident and response

The original single `15:17 UTC` trigger was not adequate as a freshness control. Its first two
scheduled events arrived many hours late even though both workflows succeeded once GitHub created
the runs:

| Slate | Declared trigger | Run created | Delivery delay | Result |
| --- | --- | --- | --- | --- |
| `2026-08-27` | `2026-08-27 15:17 UTC` | `2026-08-28 00:25:41 UTC` | 9h 08m 41s | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33129685641) |
| `2026-08-28` | `2026-08-28 15:17 UTC` | `2026-08-29 00:01:01 UTC` | 8h 44m 01s | [PASS](https://github.com/ballinprestige/ballpark-weather-lab/actions/runs/33222242434) |

The evidence points to delayed schedule-event delivery: each run started as soon as it was
created, then completed the build, deploy, and exact public readback. It does not support calling
the live app a dependable daily service. The redundant windows are a mitigation to evaluate over
seven consecutive current-day releases.

If the public release date is behind the current New York date, treat the app as stale even when
the last payload's build state was `ready`. From the workflow's **Run workflow** control, dispatch
the current date (or leave the date blank), then wait for the exact public date/hash readback. Do
not use an explicit historical date as a recovery action for the live Pages deployment.

## Release evidence

Inspect these relative paths after a local build:

- `web/dist/data/release.json`
- `web/dist/data/data.json`
- `web/dist/archive/index.json`

A valid public proof must include:

- Workflow run URL.
- Deployed Pages URL.
- Slate date.
- Release `generated_at` timestamp.
- Expected payload SHA-256.
- Successful public readback result with the same date and SHA-256.
- Desktop and mobile browser-test receipts.

The current hosted and public receipts are recorded in [verification.md](verification.md).

Check the rolling evidence gate directly against the public archive:

```bash
python -m ballpark verify-reliability \
  --url "https://ballinprestige.github.io/ballpark-weather-lab/"
```

The command verifies up to seven consecutive dates ending on the current New York date. A date
counts only when the index contains one receipt, the archived bytes pass the full publication
contract and match its lowercase SHA-256, the payload metadata agrees with the receipt, and
`generated_at` falls on that slate date in `America/New_York`. `ready`, honest `degraded`, and
valid `no_slate` payloads all count toward the archive-generation gate. The command reports
`provisional` until all seven dates pass. It does not prove when deployment occurred: retain the
corresponding successful workflow/public-readback links as the hosted execution evidence.

## Manual public readback

Given the deployed URL and a local release receipt:

```bash
python -m ballpark verify-public \
  --url "https://ballinprestige.github.io/ballpark-weather-lab/" \
  --receipt web/dist/data/release.json
```

The command is intentionally bounded. It fails if the public date is stale, the public receipt
hash differs, or the public payload bytes do not match the receipt.

## State-based response guide

### `ready`

All scheduled games have verified weather. Review the release receipt and public readback.
Approach C may still be unavailable because it is optional.

### `degraded`

At least one scheduled game lacks verified weather. The release is valid only when affected games
show a weather hold and seasonal baselines rather than a fresh weather-adjusted result. Review
Data Health and the per-game reason.

### `no_slate`

MLB reported no scheduled games. This is a valid release with zero games, not a loading failure.

### `STALE` interface warning

The browser compares the current release date with the `America/New_York` date. When the release
is behind, the interface replaces `READY`, `DEGRADED`, or `NO SLATE` with a prominent `STALE`
warning while preserving the dated payload's underlying detail. Treat that warning as an
operational failure: dispatch the current date, require the exact public date/hash readback, and
do not describe the live app as current until the warning clears.

### Command failure before publication

Expected for a missing required schedule, invalid schema, duplicate ID, or critical artifact
failure. The previous public deployment should remain in place. Diagnose the failing source or
artifact; do not weaken the contract to force a release.

### Deploy succeeds but public readback fails

Treat the release as unverified. Check the Pages environment URL, cache propagation, receipt date,
and payload hash. The verifier already retries within a bounded window. Do not report freshness
until a later readback succeeds.

## Recovery and rollback

- Re-run a transiently failed workflow only after identifying that inputs are safe and unchanged.
- For a code regression, revert through normal version control and run the complete verification
  path again.
- Do not hand-edit deployed Pages files or inject a workstation credential.
- Historical restoration is a convenience, not a backup. Preserve workflow artifacts and release
  receipts according to repository policy.
- This project publishes to a separate preview. Any change to an older production URL requires a
  separate operator decision documenting action, downside, rollback, and scope.

## Dependency maintenance

Dependabot monitors Python and npm manifests weekly. Review changes rather than auto-merging them.
`requirements.lock` fixes the transitive Python graph and package hashes; `requirements.in` is the
reviewable direct-input file. Regenerate it in a reviewed Python 3.12 environment with
`pip-compile --generate-hashes --allow-unsafe requirements.in`, then verify installation with
`--require-hashes`.
The official CPU-only XGBoost distribution keeps the hosted runtime small and excludes unused GPU
libraries. Workflow Actions are pinned to reviewed full commit SHAs.
