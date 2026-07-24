# Define the deliberate rich-corpus selection policy

depends-on: 012,013,014

## Goal

Define a transparent selection process that turns the whole known metadata inventory into a deliberately chosen, rights-eligible rich corpus for score generation. Prevent the inventory from becoming an indiscriminate bulk mirror or the one-object proof from becoming the corpus boundary.

## Acceptance tests

- Define versioned selection-candidate, selection-decision, coverage-target, exclusion, and selection-manifest contracts linked to stable source and asset IDs.
- Separate source-universe inclusion from rich-corpus selection. Every known metadata record remains countable even when content is excluded, blocked, unavailable, duplicate, or out of scope.
- Make selection criteria explicit and testable across source, period, language, medium, topic, performance context, technical quality, duplicate cluster, rights, retention, privacy, and downstream transformation eligibility.
- Require a sanitized rationale, decision authority, timestamp, selection-policy version, rights snapshot, and expiry or review trigger for every include or exclude decision.
- Prevent quality, popularity, or easy downloadability from silently overriding coverage targets, rights blockers, platform restrictions, or consent.
- Support stratified targets and documented exceptions without fixing unverified inventory counts. Report underrepresented strata and unresolved candidates.
- Exclude the pipeline proof asset from automatic selection; it may enter only through the same reviewed selection process as any other candidate.
- Add deterministic synthetic tests for stable scoring or rule evaluation, ties, missing metadata, conflicting rights, duplicate clusters, policy version changes, and reproducible selection manifests.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Selection consumes sanitized metadata and reviewed rights records only.

## Artifact outputs

- New selection and coverage schemas under `schemas/`
- New deterministic selection evaluator under `src/performing_fire_corpus/`
- New synthetic selection and bias tests
- New public selection-policy documentation under `docs/`

## Stop conditions

- Stop if selection depends on unverified total counts, private proposal material, personal identities, inaccessible content, or unreviewed model output.
- Stop if a selected asset lacks current rights, retention, privacy, and downstream-use eligibility.
- Stop if the proof object or easiest downloadable source becomes a default corpus boundary.
- Stop if selection logic cannot explain inclusion, exclusion, and coverage gaps using sanitized fields.

## Human clarification protocol

Ask only if two defensible coverage priorities produce materially different next acquisition work and no checked-in product goal resolves them. Present the affected strata and rights-safe options, recommend the more diverse bounded pilot, and leave room for a different priority.

## Recommended response

Adopt a versioned stratified policy that first filters for current authority, then balances medium, period, language, context, and source coverage. Publish gaps and exclusions rather than filling quotas with weakly authorized material.

## Trade-offs

Deliberate selection reduces volume and may leave visible gaps, but produces a more defensible corpus for score generation. Transparent criteria can expose subjective choices, which is valuable for later review and revision.

## Free-form response

Optional maintainer notes or alternate coverage priority:
