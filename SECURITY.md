# Security Policy

## Supported version

Only the latest commit on the default branch is supported. Historical snapshots are immutable
data records, not separately maintained software releases.

## Report a vulnerability

Use GitHub's private vulnerability-reporting form under the repository's **Security** tab when it
is enabled. Do not place credentials, personal information, exploit details, or non-public URLs in
a public issue. If private reporting is unavailable, open a minimal public issue requesting a
private contact channel without including sensitive details.

Include:

- Affected commit and component.
- Reproduction steps using non-sensitive fixtures.
- Expected and observed behavior.
- Security impact and any known mitigations.

No bug bounty or response-time commitment is offered.

## Security design

Ballpark Weather Lab publishes static files; it has no application server, account system,
client-side password gate, or write API. The expected trust boundaries are:

- MLB and Open-Meteo responses are untrusted network input.
- Model and derived-data files are untrusted until their SHA-256, size, and where applicable row
  count match `assets/manifest.json`.
- The generated slate is untrusted until it passes `schemas/slate.schema.json` plus semantic
  checks such as unique game IDs and a single slate date.
- Restored history is accepted only when its path date is canonical and its bytes match the
  recorded hash.
- The browser validates the release receipt and payload before rendering.

Network calls have connect/read timeouts, bounded retries, and an overall daily deadline. Missing
weather produces a visible hold; it is not silently replaced with a new model result. Missing
lineups only disable experimental context. Invalid critical artifacts, duplicate game IDs, or a
required schedule failure stop publication.

## Deployment credentials

GitHub Pages is deployed through the official Actions flow. The deploy job receives only:

- `contents: read`
- `pages: write`
- `id-token: write`

The workflow requires no personal access token, workstation token file, or application secret.
Do not add one. Any future source requiring credentials must be isolated from the public build,
use a least-privilege GitHub environment secret, and receive a documented threat review first.

## Integrity limits

SHA-256 receipts detect accidental or unexpected byte differences; they do not independently
authenticate a malicious repository maintainer or compromised Actions runner. GitHub's deployment
record and branch protections should be treated as part of the provenance chain. Model artifacts
are not cryptographically signed.

The public site is informational. It should not be used for safety-critical, medical, legal,
financial, or operational decisions.

## Dependency and release hygiene

Before publication:

1. Run Python, npm, and browser tests.
2. Verify the artifact manifest.
3. Run secret, private-identifier, dependency-vulnerability, and dependency-license scans over
   both the repository and `web/dist`.
4. Review dependency-update pull requests and regenerate lock files through their package
   managers.
5. Confirm the Pages readback date and exact payload hash.

See [the sanitization report](docs/security-sanitization-report.md) for the clean-repository threat
model and release gates.
