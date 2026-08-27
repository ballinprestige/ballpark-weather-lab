# Operator Decision: Separate Preview Publication

## Status

Authorized for a new sanitized repository and a separate GitHub Pages preview. This decision does
not authorize modification or cutover of any older production URL, scheduled task, or runtime.

## Action

Create a fresh-history repository from the allowlisted standalone project, complete the release
gates in [verification.md](verification.md), deploy through the repository's GitHub Pages workflow,
and retain the resulting repository, workflow, demo, date, and SHA-256 receipts as employer-facing
evidence.

## Scope

Included:

- Standalone schedule, game-hour weather, Approach B, optional Approach C, validation,
  publication, and static-interface code.
- Compact artifacts listed in `assets/manifest.json`, subject to the documented data-rights
  boundary.
- Synthetic fixtures, tests, model/architecture/security documentation, and new preview history.
- GitHub Actions and Pages resources belonging only to the new repository.

Excluded:

- Any existing production URL or repository visibility change.
- Legacy task deletion or enablement.
- Legacy runtime deletion, movement, or mutation.
- Unrelated research lanes, raw provider data, private history, and workstation state.

## Downside

- The preview begins with no operational history until verified daily releases accumulate.
- Some older capabilities are intentionally absent, making the preview narrower than the source
  application.
- Raw training rows are withheld, so model training is not fully reproducible from the preview.
- A separate repository creates one additional dependency-update and incident-response surface.
- Third-party provider terms and approximate geometry/profile provenance still require ongoing
  review.

## Rollback

If a preview release is unsafe or incorrect:

1. Disable the new repository's scheduled workflow or Pages environment.
2. Preserve the failed workflow, artifact, and readback receipts for diagnosis.
3. Revert the preview through normal version control and rerun all release gates.
4. Do not redirect an older production URL to the preview.

Because the older production system is outside this action, preview rollback does not require
deleting or changing its tasks, runtimes, repository, or URL.

## Evidence required to close this decision

- Fresh repository URL and root-history review.
- Passing clean-repository tests and scans.
- Passing desktop and mobile employer-demo paths.
- Pages workflow run and demo URL.
- Successful public date and exact payload SHA-256 readback.
- Replacement of all pending fields in [verification.md](verification.md).

A future production cutover is a different action and requires a new operator decision with its
own downside, rollback, and scope.
