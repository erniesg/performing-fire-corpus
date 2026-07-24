# Evaluate corpus completeness, quality, duplicates, and coverage gaps

depends-on: 017,019,021,023,028,033

## Goal

Build a reproducible evaluation suite for source-universe completeness, rich-corpus selection coverage, duplicate detection, metadata and derived quality, rights eligibility, search behavior, and unresolved gaps. Treat every count as evidence-scoped rather than inherited fact.

## Acceptance tests

- Define versioned evaluation-run, metric, coverage-gap, duplicate-finding, quality-finding, retrieval-case, and recommendation contracts with exact input snapshot and policy versions.
- Report per-source observed records, endpoint scope, pages visited, bounded remainder, blockers, duplicates, missing fields, stale evidence, and completeness state. Never convert `bounded_partial` into a whole-source total.
- Evaluate selection coverage across declared strata and report excluded, blocked, unavailable, and underrepresented candidates separately.
- Detect exact-hash duplicates, stable-ID aliases, likely metadata duplicates, and conflicting duplicates with explainable evidence and no automatic destructive merge.
- Evaluate metadata normalization, provenance completeness, rights freshness, retention readiness, derived confidence or uncertainty, deletion propagation, and index consistency.
- Add rights-filtered retrieval cases that prove allowed results appear and blocked, expired, withdrawn, or audience-ineligible fields do not appear in results, facets, or exports.
- Generate a prioritized recommendation list whose next action is one bounded adapter, rights review, correction, transformation, or index task; do not recommend bulk acquisition to fill a metric.
- Use only sanitized manifests, ledger facts, and synthetic or approved derived metadata. Aggregate reports exclude source prose, private material, provider details, credentials, and local paths.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Evaluation consumes sanitized snapshots and policy records.

## Artifact outputs

- New evaluation schemas under `schemas/`
- New deterministic evaluation modules under `src/performing_fire_corpus/`
- New synthetic metric and rights-filtered retrieval tests
- New sanitized aggregate corpus evaluation report under `docs/`

## Stop conditions

- Stop if a metric depends on an unverified historical count or inaccessible source content.
- Stop if duplicate scoring auto-merges or deletes records without review.
- Stop if a quality target encourages acquisition despite a rights, robots, platform, access, privacy, or retention blocker.
- Stop if an aggregate report can reveal protected content or private population facts.

## Human clarification protocol

Ask only if two equally bounded remediation tasks have materially different product priorities and selecting one blocks the next executable issue. Present evidence-scoped gaps, recommend the highest-impact rights-safe task, and allow a free-form priority.

## Recommended response

Prioritize current source-universe gaps and policy freshness before corpus volume. Treat blocked coverage as an explicit result, not a reason to bypass controls or lower quality and rights thresholds.

## Trade-offs

Honest completeness reporting may never yield a single satisfying percentage for open websites, but it avoids false precision. Explainable duplicate review costs more than automatic merging and preserves provenance.

## Free-form response

Optional maintainer notes or alternate evaluation priority:
