# Threat and Sanitization Report

## Purpose

This report records why the employer-facing project uses an allowlisted export and fresh history
instead of publishing the earlier application repository. It identifies classes of excluded
material without reproducing private identifiers or credential-like values.

**Source evidence commit:** `d08dee3b70cf4ca7534fa143144751f6de5f6e79`  
**Audit date:** 2026-08-27  
**Public repository history:** fresh root commit `4e9f6d8`; no inherited history or remote

## Threat model

The release process assumes risk from:

- Credentials or private identifiers embedded in source, fixtures, binary metadata, Git history,
  generated frontend files, or workflow logs.
- Workstation-specific paths, internal repository names, hosting identifiers, account names, and
  runtime snapshots.
- Third-party research and data without a documented redistribution basis.
- Large or duplicate artifacts that obscure review and increase supply-chain surface.
- Unsupported model, comparison, outcome, or financial claims.
- Static client-side controls presented as authentication.
- Optional services becoming release gates for the core park-weather slate.
- Mutable dependencies, Actions, or network responses changing a build unexpectedly.

## Source-repository audit

The pinned source tree contained 834 files and approximately 166 MB. Its reachable history
contained 251 commits and 1,842 unique blobs totaling approximately 191 MB.

High-confidence findings:

- Approximately 135 MB of third-party research material was present, including 86 PDFs totaling
  approximately 129 MB. Redistribution rights were not documented.
- Sixty-four duplicate-blob groups accounted for approximately 12.6 MB of avoidable duplication.
- The source repository had no root license or third-party notice.
- Private-environment references were widespread across configuration, scripts, plans, runtime
  captures, and generated audit files.
- A UTF-16 runtime snapshot exposed a local account identifier, operating-system security
  identifier, and absolute workstation path.
- A hosting configuration exposed a private project identifier.
- Test fixtures contained fake credential-shaped strings that could trigger repository scanners.
- Seventeen archived commercial-feed payloads contained dated provider-derived values and capture
  history.
- The existing dashboard payload and interface contained unrelated recommendation and research
  fields outside the park-weather scope.
- One legacy static page used a client-side hash/session-storage gate that did not provide real
  authentication.
- Historical documentation contained unsubstantiated competitor and model-performance language.

A signature-oriented scan of all reachable blobs found no high-confidence live credential. That
negative finding is not a guarantee and does not justify publishing the old history.

## Clean publication strategy

The employer-facing repository must be created from an explicit allowlist and a new repository
root. It must not be a fork, filtered-history copy, or public conversion of the source repository.
The only retained source-history reference is the evidence commit hash above.

Included categories are limited to:

- Standalone schedule, weather, Approach B, optional Approach C, contract, artifact, publication,
  and CLI modules.
- The sanitized Svelte interface.
- Synthetic fixtures and focused tests.
- Compact model and derived artifacts listed in `assets/manifest.json`.
- Public documentation, architecture, limitations, and this report.
- A least-privilege GitHub Pages workflow.

Excluded categories include:

- Third-party research documents and office files.
- Runtime snapshots, task exports, operator state, and workstation configuration.
- Commercial-feed archives, related adapters, schemas, and UI fields.
- Injury, pitcher-research, external scorer, and multi-stage orchestration lanes.
- Raw training, historical weather, batted-ball, and API response rows.
- Provider caches, browser captures, old screenshots, and quarantine data.
- Local credential loaders, token files, access-control scripts, and client-side password gates.
- Private plans, handoffs, hosting metadata, repository names, owner identifiers, and absolute
  paths.
- The earlier Git history and remote configuration.

## Claims policy

Allowed evidence is precise and bounded:

- 21,608 total model rows: 17,075 fit, 2,302 validation, and 2,231 held-out test.
- Recorded validation and held-out RMSE in multiplier units, framed as one experiment.
- 839 batter profiles, 3,018,625 generated trajectory rows, and 30 stadium geometries as inventory
  counts, not accuracy claims.
- Historical isolated smoke completion of 15 games on August 26, 2026 and seven games on August
  27, 2026, explicitly separated from current public proof.

Prohibited claims include superiority, guaranteed accuracy, causal interpretation, outcome
prediction, or financial performance. Approach C must remain experimental and never headline.

## Third-party and data controls

- Row-level training and provider data is withheld.
- Included artifacts carry hashes, sizes, row counts where appropriate, criticality, and source
  boundary notes.
- Open-Meteo attribution and link must remain visible in the documentation and public interface.
- Retrosheet acknowledgment and provider terms are linked in the notices.
- Geometry and profile provenance limitations are disclosed.
- The repository's no-license terms do not purport to grant rights in third-party material.

## Residual risks and publication gates

The following must be verified against the final repository and built site:

- [x] Fresh root commit with no inherited history or private remote.
- [x] Secret scan has no unresolved findings in the sanitized local candidate.
- [x] Private-identifier scan has no account names, email addresses, absolute workstation paths,
      internal hostnames, private project IDs, or retired product/repository names.
- [x] `web/dist` passes the same scans.
- [x] No raw or archived third-party provider payload is selected for the fresh-history export.
- [x] Dependency vulnerability and license review is recorded.
- [x] GitHub Actions references are pinned to reviewed commit SHAs.
- [x] Python transitive dependencies are pinned with package hashes; the official CPU-only XGBoost
      distribution excludes unused GPU runtime packages.
- [x] Open-Meteo attribution is visible in the rendered interface.
- [x] Python, frontend, schema, artifact, and desktop/mobile tests pass locally.
- [ ] Pages deploys through OIDC without a personal token.
- [ ] The public date and payload SHA-256 match the local release receipt.
- [ ] Repository and demo URLs replace the pending placeholders.

Current completion evidence belongs in [verification.md](verification.md); this report does not
invent it.

## Documentation-specific check

The employer-facing documentation and Dependabot configuration were checked on 2026-08-27 as a
separate, narrow scan:

- Sixteen owned files were present and readable.
- No local account name, email address, absolute workstation path, sync-folder path, retired
  product/repository name, credential-shaped token, private-key marker, or credential-bearing URL
  was found.
- Every relative Markdown link resolved inside the staged repository.
- Comparison, certainty, accuracy, outcome, and financial language was reviewed. Remaining uses
  are limitations, prohibited-claim rules, or explicit denials rather than affirmative claims.
- Repository, demo, workflow, date, hash, and timestamp placeholders remain visibly pending.

This check covers the documentation files, not the final repository history or `web/dist`. The
whole-repository and built-site scans remain publication gates above.
