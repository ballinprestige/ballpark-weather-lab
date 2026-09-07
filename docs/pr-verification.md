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
After checkout, the job fails unless `git rev-parse HEAD` equals the requested PR-head SHA. Its
summary receipt records the event, event ref, event merge SHA (`github.sha`), requested PR-head
SHA, actual checkout SHA, PR number/ref, UTC start, runtime versions, and SHA-256 identities of
`requirements.lock`, `.github/requirements-verify.txt`, and `web/package-lock.json`. The event
merge SHA and tested head SHA are
deliberately distinct fields: a `pull_request` check can be associated with GitHub's merge
identity while this workflow deliberately checks out the proposed head tree. A hosted receipt and
strict up-to-date rule are needed to prove which revision GitHub requires before merge.

The runner installs Python 3.12 and Node 22.12.0. A shell guard rejects a Node version outside
the repository range `>=22.12, <23`; Node 24 is not a supported result. Dependencies are
installed with `pip --require-hashes` and `npm ci`. The commands are fail-closed (`set -euo
pipefail`) and run every discovered Python regression/contract test rather than a historical test
count, artifact verification, frontend type/unit/build/budget verification, and the current
desktop/mobile Chromium suite. The Python verification step unconditionally runs
`python scripts/check_verify_workflow.py` before test discovery. No job or required step may use
an `if` condition or `continue-on-error` value/expression.

The validator uses PyYAML 6.0.2 from the separate, hash-pinned
`.github/requirements-verify.txt`; it does not alter application dependencies or
`requirements.lock`. It safely parses one YAML document, rejects aliases, merge keys, and duplicate
mapping keys, then compares the parsed document to the complete reviewed workflow template.

## Literal clean-run validation

Run these commands from a fresh checkout with Python 3.12 and Node in the declared range. They
validate the same local code paths; they are not a hosted pull-request receipt or proof that a
remote ruleset is enforced.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --disable-pip-version-check --require-hashes -r requirements.lock
python -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes \
  -r .github/requirements-verify.txt
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
author machine without Bash). Other focused mutations make the job conditional, hide `pytest` in
an `if false` shell branch, use expression-form `continue-on-error`, add a `write-all` second job,
use spaced YAML keys, add an ignored bare list item, replace Bash with a failure-swallowing shell,
overwrite the checkout receipt variable, alter triggers, or use duplicate/alias YAML constructs.
Each must fail the validator. It recognizes only the exact semantic job, step, permission,
action-input, environment, shell, trigger, and command structure. Any workflow shape change
requires an explicit template/test update.

These are safe local emulations of failure propagation, not a substitute for opening an untrusted
hosted PR with a failing change.

Validate GitHub Actions syntax separately with a current `actionlint` installation. One
containerized invocation is:

```bash
docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.7 \
  .github/workflows/verify.yml
```

`actionlint` checks general workflow/YAML syntax; the repository contract validator checks the
allowlisted effective verification graph, including unskippable required job/steps and exact
read-only permissions. Neither local command can show a GitHub-hosted check or remote branch
protection result.

## Prepared remote configuration proposal — not activated

An authorized repository administrator should configure the following only after a hosted
untrusted PR displays the exact expected check context and source. This is a proposal, not an
activation request:

| Setting | Proposal |
| --- | --- |
| Scope | An active branch ruleset targeting `refs/heads/main`, covering direct updates and pull-request merges. Require a pull request before merge. |
| Required verification | Require the job context **PR verification** and enable strict “branches must be up to date before merging.” Select **GitHub Actions** as the expected source app rather than “any source,” using the source selector on the first hosted check run. Record the selected app/integration identity from the saved ruleset receipt; do not guess an API `integration_id` before GitHub exposes it for this repository. |
| Workflow integrity | Require code-owner review for `.github/workflows/verify.yml`, `scripts/check_verify_workflow.py`, `tests/test_verify_workflow.py`, and `docs/pr-verification.md`. The administrator must bind these patterns to an existing named security/release-owner team in `CODEOWNERS`; no team identity is invented in this packet. Require an approving review after the most recent push and dismiss stale approvals when a new commit is pushed. |
| Deployment boundary | Keep the PR workflow without an environment. Restrict the existing `github-pages` environment to `main` publication runs and retain Pages/id-token write permissions only in the existing deploy job. |
| Bypass | Do not grant routine bypass actors. Any administrator bypass remains a repository-policy decision and must be logged/reviewed; this YAML cannot prevent it. |

GitHub documents that a skipped job is reported as successful for required checks. The fixed
workflow's exact job/step contract and the workflow-integrity review controls therefore matter:
a check-name-only rule does not independently prove that arbitrary future workflow edits executed
the intended tests. The strict requirement also needs a hosted PR receipt to confirm whether
GitHub evaluates the merge identity, the head identity, or both after an update to `main`.

Repository feature availability varies by GitHub plan and organization policy. If the repository
does not offer expected-source selection, required code-owner review, stale-approval dismissal, or
most-recent-push approval in its applicable tier, record that missing control as a release risk and
obtain a concrete administrator decision; do not silently substitute a weaker “any source” or
old approval rule. Organization-level required-workflow controls, if available, can add defense in
depth but do not replace the repository's hosted evidence or the branch rules above.

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
