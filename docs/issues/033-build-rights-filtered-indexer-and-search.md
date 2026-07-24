# Build the rights-filtered indexer and search surface

depends-on: 031,032

## Goal

Implement deterministic indexing and a minimal searchable CLI or local API that returns provenance-aware, rights-filtered results for metadata and approved derived content. Provide safe score-generation exports without exposing blocked or private fields.

## Acceptance tests

- Build indexes only from validated ledger snapshots and exact verified R2-derived manifests; reject unverified objects, stale policy, missing provenance, and local media paths.
- Upsert deterministically by stable document ID and policy snapshot, remove superseded fields, and make restart or repeated indexing byte- or logically identical.
- Evaluate audience, operation, rights, consent, retention, freshness, and deletion filters before ranking or result serialization. Unknown or conflicting policy returns no protected field.
- Return stable IDs, concise approved metadata, provenance edges, evidence scope, selection status, coverage state, duplicate-cluster references, and rights-safe snippets only when explicitly permitted.
- Provide score-generation export as stable IDs, approved structured features, exact derived object keys when the consumer is authorized, and policy snapshot; never export source bytes, full prose, captions, transcripts, personal data, signed URLs, or machine-local paths.
- Prevent inference leaks through counts, facets, errors, timing fixtures, or duplicate clusters for records the caller is not allowed to know.
- Apply revocation, withdrawal, expiry, source correction, and exact deletion events idempotently and prove removed fields do not appear in search, facets, exports, or cached snapshots.
- Add offline synthetic integration tests for indexing, ranking determinism, rights-filtered queries, provenance, duplicates, deletion propagation, score export, and restart.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None for the local reference implementation and tests. Hosted authentication and deployment are outside this issue.

## Artifact outputs

- New deterministic indexer and local search modules under `src/performing_fire_corpus/`
- New local CLI or loopback API commands under the existing package
- New synthetic end-to-end search, deletion, and score-export tests
- New search and reviewer replay documentation under `docs/`

## Stop conditions

- Stop if index construction requires broad R2 listing rather than exact manifest keys.
- Stop if a result can expose a field without current audience and operation authority.
- Stop if deletion or revocation leaves a field in results, facets, cached snapshots, or exports.
- Stop if the implementation implies a hosted operator UI or production authentication exists.

## Human clarification protocol

Ask only if choosing CLI versus loopback API is necessary for the next executable integration and repository evidence does not identify a consumer. Recommend a CLI-first deterministic surface and leave room for a loopback-only API; do not claim hosted availability.

## Recommended response

Implement a CLI-first local search surface backed by deterministic synthetic fixtures. Add a loopback API only as a thin equivalent interface and label it clearly as non-hosted.

## Trade-offs

A local reference surface is less convenient than a hosted UI but validates the data and rights contract without premature auth or deployment. Strict filtering may reduce recall while preventing unauthorized disclosure.

## Free-form response

Optional maintainer notes or alternate local search surface:
