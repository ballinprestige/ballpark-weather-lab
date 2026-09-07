# Pull-request verification

`PR verification` is a nondeploying workflow for proposed changes to `main`.
It is deliberately separate from [the Pages publication workflow](../.github/workflows/pages.yml):
the PR job has only `contents: read`, checks out the exact pull-request head SHA without
persisting credentials, and has no environment, Pages/OpenID permission, deployment action,
artifact upload, restore, daily-publication, public-readback, or secret interpolation step.
It uses `pull_request`, never `pull_request_target`, because the checked-out revision is
untrusted.

## What runs

The workflow name and job/check label are both **PR verification**. It triggers for opened,
synchronized, reopened, and ready-for-review pull requests whose target is `main`. A per-PR
concurrency group cancels a superseded proposed revision; the one job has a 30-minute bound.
The job writes a GitHub Actions summary receipt containing the event, PR number, proposed ref,
exact SHA, UTC start, Python and Node versions, and SHA-256 identities of `requirements.lock`
and `web/package-lock.json`.

The runner installs Python 3.12 and Node 22.12.0. A shell guard rejects a Node version outside
the repository range `>=22.12, <23`; Node 24 is not a supported result. Dependencies are
installed with `pip --require-hashes` and `npm ci`. The commands are fail-closed (`set -euo
pipefail`) and run every discovered Python regression/contract test rather than a historical test
count, artifact verification, frontend type/unit/build/budget verification, and the current
desktop/mobile Chromium suite. No required step uses `continue-on-error`.

## Literal clean-run validation

Run these commands from a fresh checkout with Python 3.12 and Node in the declared range. They
validate the same local code paths; they are not a hosted pull-request receipt or proof that a
remote ruleset is enforced.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check --require-hashes -r requirements.lock
python -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e .
npm ci --prefix web
python -m ruff check src tests scripts
python -m pytest
python -m ballpark verify-artifacts
npm run verify --prefix web
npx --prefix web playwright install --with-deps chromium
npm run test:e2e --prefix web
```

The focused workflow contract is included by ordinary `python -m pytest` discovery. To inspect it
on its own and demonstrate a fail-closed intentional defect, run:

```bash
python scripts/check_verify_workflow.py
python -m pytest tests/test_verify_workflow.py
```

The final test deliberately runs a process that exits 23 and asserts that its following command
is not reached (`bash -euo pipefail` on the Linux runner; `cmd`'s `&&` equivalent on a Windows
author machine without Bash). A second test mutates the workflow to narrow test discovery and
confirms that the validator exits nonzero. These are safe local emulations of failure propagation,
not a substitute for opening an untrusted hosted PR with a failing change.

Validate GitHub Actions syntax separately with a current `actionlint` installation. One
containerized invocation is:

```bash
docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.7 \
  .github/workflows/verify.yml
```

`actionlint` checks workflow structure; the repository contract validator checks this workflow's
security and execution boundary. Neither local command can show a GitHub-hosted check or remote
branch protection result.

## Prepared remote configuration proposal — not activated

An authorized repository administrator should configure the following only after a hosted
untrusted PR displays the exact expected check context:

| Setting | Proposal |
| --- | --- |
| Scope | An active ruleset targeting the default branch `main`, including direct updates and pull-request merges. |
| Required verification | Require the **PR verification** check from this repository's workflow for the exact PR head revision; enable the setting that requires the branch to be up to date before merge. Confirm the exact GitHub-rendered context from the hosted receipt before saving it. |
| Deployment boundary | Keep the PR workflow without an environment. Restrict the existing `github-pages` environment to `main` publication runs and retain Pages/id-token write permissions only in the existing deploy job. |
| Bypass | Do not grant routine bypass actors. Any administrator bypass remains a repository-policy decision and must be logged/reviewed; this YAML cannot prevent it. |

**Benefit:** an untrusted proposed change must complete the nondeploying verification lane before
normal promotion to `main`, while Pages publication remains a separate push-only path.

**Downside:** Chromium installation and the full suite can add queue/runtime cost, and a broken
third-party runner/network dependency can temporarily block merges. The cancellation group means
only the latest pushed revision of one PR remains required.

**Wait/no-action outcome:** without the remote ruleset, the workflow can run and report failure
but a repository setting may still allow merge without it. Existing `main` publication behavior is
unchanged by this packet.

**Rollback:** disable or edit the ruleset/check requirement, then restore the prior protection
configuration from its recorded settings receipt. Do not remove the workflow merely to bypass a
failed change; investigate the failing revision first.

This proposal does not activate settings, create a PR, prevent administrator bypass, or prove a
hosted check. REL-04 stays open until an authorized configuration inspection and a hosted
intentional-failure receipt verify enforcement. Fast PR checks, broader release checks, and the
separate seven-day real-observation gate are distinct evidence classes; this workflow makes no
seven-day or next-day reliability claim.
