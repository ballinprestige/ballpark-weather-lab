# Incident and Repair Case Study

## Summary

A polished public park-weather surface became stale even though its standalone builder still
worked. The incident was not primarily a modeling failure. The publication wrapper had accumulated
so many unrelated operational dependencies that optional research lanes could block the useful
park-weather slate.

The repair separated the functional critical path into this standalone project, made optional
context non-blocking, added explicit contracts and release receipts, and prepared a new preview
without changing the older production system.

## Observed condition

- The public site did not reliably advance to the current slate.
- 154 legacy Windows scheduled tasks sharing the broader application label were disabled.
- Daily publication depended on multiple runtimes, a nine-stage orchestrator, staging/snapshot
  promotion, external proof state, and optional research integrations.
- Operators could not distinguish a real schedule/weather failure from an unrelated optional-lane
  failure by looking at the public surface.

The cost was operational ambiguity: a working park-factor builder looked broken because its wrapper
could not complete.

## Diagnostic isolation

Publishing was disabled and the standalone builder was run in an isolated runtime.

- On August 26, 2026, it completed all 15 scheduled games with confirmed lineups.
- On August 27, 2026, it completed all seven scheduled games with valid weather before lineups were
  available and reported the optional path as unavailable.

This established two facts:

1. Schedule → weather → Approach B was functional.
2. Lineup availability did not need to be a release prerequisite.

The smoke runs were historical diagnostic evidence. They did not deploy a public site and do not
substitute for verification of this repository.

## Root cause

The operational dependency graph had confused product completeness with publication safety.
Several optional services had become hard gates even though none was necessary to answer the
product's central question: how do venue and game-hour weather affect the slate's park factors?

Disabled task state, runtime divergence, multi-stage promotion, and external proof dependencies
then amplified one another. The system lacked a single payload contract and a public date/hash
readback that could answer whether the correct bytes were actually live.

## Repair

The repair reduced the daily graph to:

```text
schedule + game-hour weather
  -> Approach B weather-adjusted park factors
  -> optional Approach C context when all optional evidence exists
  -> validate one payload
  -> build one static frontend
  -> deploy one Pages artifact
  -> verify the public date and exact payload hash
```

Specific controls include:

- A required/optional artifact inventory with hashes, sizes, and row counts.
- Explicit `ready`, `degraded`, and `no_slate` publication states.
- Per-game weather holds that retain seasonal baselines without presenting neutral fallback as a
  fresh modeled result.
- Approach C labeled experimental, optional, and `used_in_headline: false`.
- JSON Schema and semantic validation for unique IDs, one slate date, and cross-field state.
- Canonical payload bytes, a SHA-256 release receipt, dated history, and bounded public readback.
- One daily CLI command and one least-privilege GitHub Pages workflow.
- Deterministic fixtures for normal, empty, degraded, optional-data, malformed-artifact, and
  duplicate-ID paths.

## Why this repair is safer

The application now fails according to the evidence boundary:

- Missing required schedule: stop and preserve the prior release.
- Missing weather for one game: publish an honest per-game hold.
- Missing lineup or optional physics data: keep the core slate and disable experimental context.
- Invalid critical artifact or payload: fail closed.
- Stale or mismatched Pages deployment: fail the readback check.

This makes the failure visible at the layer that caused it and prevents optional work from
masquerading as core unavailability.

## Reliability follow-up: the builder was fixed before the schedule was proven

The first two unattended GitHub schedule events both completed successfully, but GitHub did not
create either run until roughly nine hours after its declared cron time. On August 28 the prior
day still appeared as `READY` until the delayed run finally published 15 current games. That
exposed two separate control gaps: one scheduler opportunity was not enough, and a payload's build
state was being confused with its current-date freshness.

The follow-up repair therefore:

- Adds staggered morning, midday-recovery, and late-lineup schedule opportunities.
- Records trigger delivery metadata separately from build/deploy duration.
- Replaces the live release state with `STALE` whenever the payload date trails the current New
  York date, while preserving the underlying payload detail.
- Verifies a rolling seven-date ledger from public, hash-matched archive bytes and keeps the daily
  service claim `provisional` until all seven dates pass with corresponding hosted readbacks.

These controls mitigate and expose scheduler delay; they do not eliminate GitHub's documented
best-effort schedule behavior or establish a new independent scheduler failure domain.

## Scope and cutover decision

The authorized action is a separate sanitized preview repository and Pages proof.

| Decision field | Position |
| --- | --- |
| Action | Publish and verify the new standalone preview only. |
| Scope | New clean history, narrow source tree, model/fixture evidence, static demo, and hosted daily workflow. |
| Downside | Older operational history and unrelated features are intentionally absent; the preview starts with no public archive until daily releases accumulate. |
| Rollback | Disable the preview workflow or Pages environment and revert the preview commit through normal version control. |
| Existing production | Unchanged. No legacy task, runtime, or public production URL is deleted or cut over. |

Any later cutover of an existing production URL requires a separate operator decision with a
fresh action, downside, rollback, and scope analysis.

## Publication evidence

The standalone architecture repair is demonstrated by clean-repository tests, hosted
desktop/mobile browser receipts, successful Pages workflows, the live demo, and exact public
date/hash proofs listed in [verification.md](verification.md). Its unattended daily reliability is
not yet proven: the rolling gate is `2/7` as of August 28. No existing production URL, task,
runtime, or repository was cut over.
