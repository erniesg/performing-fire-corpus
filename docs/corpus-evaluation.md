# Corpus evaluation: completeness, quality, duplicates, and coverage

This contract defines how the project measures itself, and records the current
aggregate result. Every number here is evidence-scoped: it names the exact
snapshot it was read from, and it does not travel outside that snapshot.

`src/performing_fire_corpus/evaluation.py` implements it, and
`tests/test_corpus_evaluation.py` proves it over synthetic records only. The
module contacts no source, no object store, and no index service.

## Versioned contracts

| Record | Schema | Purpose |
|---|---|---|
| `evaluation_run` | `schemas/v1/evaluation-run.json` | One reproducible run, its exact input snapshot, and its policy versions |
| `evaluation_metric` | `schemas/v1/evaluation-metric.json` | One evidence-scoped count with its completeness state |
| `coverage_gap` | `schemas/v1/coverage-gap.json` | One declared stratum with its causes counted separately |
| `duplicate_finding` | `schemas/v1/duplicate-finding.json` | One explainable duplicate that merges nothing |
| `quality_finding` | `schemas/v1/quality-finding.json` | One metadata, rights, retention, derived, or index check |
| `retrieval_case` | `schemas/v1/retrieval-case.json` | One rights-filtered retrieval case and its observed outcome |
| `evaluation_recommendation` | `schemas/v1/evaluation-recommendation.json` | One bounded next action |

The run envelope binds identity, the input snapshot, and the policy versions.
Every nested finding is additionally admitted against its own strict record
schema by `validate_evaluation_run`, which also recomputes every content-bound
identifier and refuses any reference it cannot resolve.

`input_snapshot` pins the exact completeness report IDs, discovery run IDs,
policy snapshot IDs, selection manifest ID and policy version, inventory
snapshot hash, corpus index ID, index snapshot ID, index hash, and policy
snapshot hash. A run with no index in scope reports empty index sections rather
than a clean result.

## Counting rules

A bounded observation is never widened into a total. `is_whole_source_total`
may be `true` only when three conditions hold at once:

1. every canonical endpoint of that source reported
   `complete_for_observed_endpoint`;
2. no endpoint of that source is missing from the run; and
3. a human has declared that source's reviewed endpoint list exhaustive.

For an open website the third condition is normally absent, so the honest
answer stays "not a whole-source total". An endpoint-scoped metric is never a
whole-source total whatever its state. A `bounded_partial`, `blocked`,
`changed`, or `unknown` metric carries a null denominator and cannot claim a
total; the schema and the runtime validator both refuse it.

An unknown remainder is reported as unknown, never as zero. A complete endpoint
may still have an unknown remainder. Blocked pages are an explicit result, not
a reason to raise a bound.

## Coverage evaluation

Each declared stratum reports observed, eligible, selected, excluded, blocked,
unavailable, and unresolved candidates as separate counts, then one dominant
`state`: `met`, `blocked`, `unavailable`, `unresolved`, or `underrepresented`,
in that precedence. A met stratum carries no next action. Blocked coverage is
reported, not bypassed: a blocked or unavailable stratum never yields a bounded
adapter run.

## Duplicate detection

Four evidence classes, each with an explainable summary:

- `exact_hash_duplicate` — two records bind derived objects with the same exact
  content hash.
- `stable_id_alias` — one stable item identifier appears under more than one
  source.
- `likely_metadata_duplicate` — every shared metadata field hash matches.
- `conflicting_duplicate` — the records agree on some shared field hashes and
  disagree on others.

Every finding is `review_state: requires_human_review` with
`merge_action: none`, both fixed literals in the schema. No automatic
destructive merge exists, and nothing is deleted. Provenance of every member is
preserved and remains countable.

## Quality checks

| Check | Reports |
|---|---|
| `metadata_normalization` | missing required fields, and unresolved declared dimensions, separately |
| `provenance_completeness` | derived lineage that reaches outside its own record, so a fusion must be confirmed |
| `rights_freshness` | current authority that is missing, revoked, or drifted; not-yet-effective grants; expired rights or provenance evidence |
| `retention_readiness` | retention class that disagrees with the record's selection state |
| `derived_confidence` | generated fields with no verified derived object behind them; model output is not ground truth |
| `deletion_propagation` | a removal that left an empty record shell, so propagation cannot be proven from this snapshot |
| `index_consistency` | a field or record that left the index with no deletion event authorizing its removal |

Rights freshness needs a current authority boundary. Without one it reports
staleness only: a revoked or withdrawn grant is invisible to a snapshot that
still carries it. An unproven outcome is `unknown`, never `pass`.

## Rights-filtered retrieval cases

A case declares its audience, query terms, filters, the fields that must be
visible, the fields that must appear nowhere, and the facet values that must
appear nowhere. It is then evaluated against results, facets, and the
score-generation export, and fails with one exact reason per surface:
`expected_field_missing`, `forbidden_field_in_results`,
`forbidden_facet_value`, or `forbidden_field_in_export`. A case cannot expect
and forbid the same field.

## Recommendation rules

Recommendations are grouped by cause and ordered by priority: current rights
and policy freshness first, then provable index behaviour, then retention,
metadata, and derived review, then duplicate review, and only then one bounded
adapter run. The action vocabulary contains no bulk-acquisition action at all.
A `bounded_adapter_run` may not carry a blocker class, and may not reference a
blocked observation or a blocked or unavailable stratum; the schema and
`validate_evaluation_run` both refuse it. A durable blocker becomes a
`human_decision`, never an acquisition.

## Current aggregate evaluation report

Evidence-scoped as of 2026-07-26. Derived from the sanitized reports already in
this repository: `docs/njp-center-site-inventory-report.json`,
`docs/metadata-readiness-proof.md`, `docs/antiegg-metadata-adapters.md`, and
`docs/njp-video-library-inventory.md`. No live source was contacted to produce
this section, and no historical count was inherited as a current fact.

Source universe, per observed endpoint:

| Source | Endpoint | Observed unique records | State | Blocker |
|---|---|---|---|---|
| `njp-center-main` | `njp-center-main-home` | 29 | `complete_for_observed_endpoint` | none |
| `njp-center-video-archive` | `njp-center-video-archive-page` | 0 | `blocked` | transport error, then `source_shape_unreviewed` |
| `njp-video-library` | `njp-video-library-home` | 0 | `blocked` | `robots_ambiguous` |
| `antiegg-fluxus` | `antiegg-article` | 0 | `blocked` | `response_oversized` |
| `antiegg-fluxus` | `antiegg-posts-api` | 2 | `bounded_partial` | none; portable fixture audit only |
| `njp-youtube-official` | — | — | `unknown` | no observation; issue 023 is `rucksack-blocked` |

Per-source aggregates:

- No source is a whole-source total. `njp-center-main` completed its one
  reviewed endpoint, but its endpoint list is not declared exhaustive, so the
  29 records remain an endpoint observation. This matches
  `docs/njp-center-site-inventory-report.json`: "their counts do not measure
  the whole NJP Center universe."
- Every bounded remainder is unknown. No source states a remainder.
- `antiegg-fluxus` aggregates to `blocked`, because one of its endpoints is
  blocked. A partially bounded endpoint does not lift the source state.

Selection coverage, duplicates, quality, and retrieval:

- No selection manifest and no corpus index snapshot exist in this repository.
  Those sections are empty rather than clean. No corpus has been selected, no
  duplicate has been reviewed, and no rights-filtered retrieval case has been
  run against real data.
- The selection, index, and retrieval contracts are `implemented-offline`
  against synthetic fixtures only. See `docs/rich-corpus-selection.md`,
  `docs/provenance-aware-search-index.md`, and
  `docs/rights-filtered-search-surface.md`.

Prioritized next actions:

| Priority | Next action | Blocker | Source | Why |
|---|---|---|---|---|
| 3 | `human_decision` | `access` | `njp-center-video-archive` | The Video Archive shape is unreviewed and the bounded check ended in a transport error. Reviewing that shape is a human decision, not a retry. |
| 3 | `human_decision` | `access` | `njp-video-library` | `/robots.txt` returns the SPA `index.html`, so the adapter refused to interpret it. Resolving the ambiguous response comes before any catalogue request. |
| 3 | `human_decision` | `access` | `antiegg-fluxus` | The one article endpoint ended on a durable `response_oversized` blocker. |
| 7 | `bounded_adapter_run` | `none` | `antiegg-fluxus` | The reviewed `/wp-json/wp/v2/posts` endpoint has recorded governance and a terminating fixture audit, so one separately authorized bounded live inventory is the next safe step. |

No recommendation asks for bulk acquisition, and no recommendation asks to
acquire past a blocker.

## Stop conditions

- A metric that would depend on an unverified historical count or inaccessible
  source content is not produced. The state stays `unknown`.
- Duplicate scoring never merges or deletes. Every finding requires human
  review.
- A quality or coverage shortfall never authorizes acquisition past a rights,
  robots, platform, access, privacy, or retention blocker.
- Aggregate reports carry only record identifiers, integers, fixed literal
  states, and sanitized rationale text. Source prose, private material,
  provider details, credentials, and local paths are excluded by the schema
  character allowlist and by the central redaction module, and the rendered
  report is re-sanitized before it is returned.
