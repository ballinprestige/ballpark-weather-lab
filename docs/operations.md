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

`.github/workflows/pages.yml` runs daily at `15:17 UTC`, on pushes to `main`, and through manual
dispatch. Manual dispatch accepts an optional canonical `YYYY-MM-DD` date.

The workflow:

1. Installs the locked direct dependencies.
2. Runs Python tests, artifact verification, Svelte checks, and frontend unit tests.
3. Restores valid public history when available.
4. Runs `python -m ballpark daily`.
5. Uploads `web/dist` as one Pages artifact.
6. Deploys with GitHub's Pages identity.
7. Reads back the public date and exact payload SHA-256 with bounded retries.

Concurrency uses one `pages` group and does not cancel an in-progress deployment.

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
