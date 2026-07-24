# Design the provenance-aware rights-filtered search index

depends-on: 013,027,028

## Goal

Define a searchable index that unifies the complete known metadata inventory with the deliberately selected rich corpus while preserving provenance, coverage status, duplicate relationships, rights, consent, retention, and retrieval visibility.

## Acceptance tests

- Define versioned index-document, provenance-edge, duplicate-cluster, visibility-policy, deletion-event, and index-snapshot contracts keyed by stable source, asset, object, and transformation IDs.
- Separate inventory-only metadata from selected raw or derived corpus fields. A result identifies which fields are factual source metadata, derived observations, generated scores, or project-native records.
- Require every indexed field to carry source provenance, evidence time, rights and consent snapshot, retention class, visibility class, and deletion or review trigger.
- Define query-time filters for operation, audience, rights, consent, source, language, period, medium, selection state, duplicate cluster, and evidence freshness. Missing or stale authority excludes protected fields.
- Define canonicalization and duplicate semantics without collapsing source-specific records. A cluster has explainable evidence and retains all provenance edges.
- Support exact deletion and reindex work for rights revocation, consent withdrawal, source correction, retention expiry, and transformation replacement.
- Keep raw source objects, media, full prose, credentials, signed URLs, private proposal material, and machine-local paths out of index documents.
- Add deterministic synthetic index and query-policy fixtures proving rights-filtered behavior, stale-policy exclusion, provenance rendering, duplicate clusters, and deletion propagation.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Index design and tests use synthetic sanitized records.

## Artifact outputs

- New search, provenance, duplicate, visibility, and snapshot schemas under `schemas/`
- New checked-in index design under `docs/`
- New synthetic query-policy and deletion fixtures under `tests/`
- New index contract validators under `src/performing_fire_corpus/`

## Stop conditions

- Stop if an indexed field cannot identify its provenance, current authority, retention, and visibility.
- Stop if duplicate handling erases source-specific rights, corrections, or completeness accounting.
- Stop if raw source content, private material, credentials, signed locators, or local paths would enter the index.
- Stop if an unknown or stale rights state defaults visible.

## Human clarification protocol

Ask only if two retrieval audiences need conflicting visibility defaults and the choice blocks index implementation. Recommend the most restrictive default with explicit audience grants and provide room for a reviewed alternative.

## Recommended response

Use field-level provenance and policy snapshots with query-time fail-closed filtering. Keep inventory metadata searchable even when rich content is blocked, while clearly marking coverage and selection state.

## Trade-offs

Field-level policy and provenance increase index size and query complexity, but allow precise deletion and rights filtering. Preserving duplicate source records may reduce apparent result simplicity while improving auditability.

## Free-form response

Optional maintainer notes or alternate index visibility rule:
