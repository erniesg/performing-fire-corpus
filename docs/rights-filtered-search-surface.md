# Rights-filtered indexer and local search surface

This is the CLI-first local reference surface for the corpus. It is not a
hosted operator UI, there is no production authentication, and nothing here
retrieves source bytes, lists an object store, or reads machine-local media.
Everything below runs offline against reviewed synthetic fixtures.

Read [`provenance-aware-search-index.md`](provenance-aware-search-index.md)
first: it defines the field-level records this surface consumes.

## What the indexer will accept

`build_corpus_index` admits one already-validated index snapshot and nothing
else. A snapshot only reaches it after `validate_index_snapshot` has
recomputed every cross-record identity, snapshot hash, ordering, authority
hash, evidence window, deletion target, and duplicate membership.

At index time the indexer re-resolves every document, provenance edge,
visibility policy, and deletion event through the trusted authority boundary
and requires exact equality with the snapshot. It then re-evaluates rights,
consent, retention, and evidence freshness at the index `built_at`. A missing,
stale, corrected, revoked, or expired record stops the build; it never
silently drops a field.

Derived content is bound by exact manifest keys only. Each derived object
declares its own source, asset, transformation, digest, and byte size, and the
key must be the exact `<prefix>/v1/derived/<source>/<asset>/<transform>/<sha>`
form built from that identity. The indexer resolves the current object receipt
for that exact key and requires a verified receipt that mirrors every field. A
missing receipt, a mismatched digest or byte size, a blocked retrieval
decision, an absolute or traversal path, a `file:`/`http:`/`s3:` locator, or a
machine-local path fails closed. No prefix listing, and no broad enumeration,
is ever performed or required.

A derived object must also be backed by index provenance: the bound document
needs at least one `derived_observation` or `generated_score` field whose
provenance edge names the same transformation. Derived bytes with no indexed
provenance are not indexable.

## Deterministic upsert and restart

Documents are keyed by their stable `index_document_id`. The index records the
`snapshot_sha256` of its source snapshot and the `policy_snapshot_sha256` of
the exact visibility policies it was built from, and binds the whole
generation with `index_sha256`.

Passing the prior generation as `previous_index` produces the upsert
bookkeeping: `upserted_document_ids` for changed or new documents,
`removed_document_ids` for documents the new snapshot no longer carries, and
`superseded_fields` for exact fields that were present before and are gone
now. Re-running the same inputs is byte-identical: the same generation rebuilt
twice serializes to the same canonical bytes, an upsert against an unchanged
prior generation reports nothing upserted or superseded, and a generation
written to disk and read back validates to the same record. Restart is
therefore a no-op rather than a new index.

## Query-time filtering before ranking

`search_corpus_index` never trusts the stored generation as visibility
authority. It re-runs full query-time authority for every field through
`query_index`, so audience, operation, rights, consent, retention, evidence
freshness, policy expiry, and exact deletion events are all evaluated before
anything is ranked or serialized. Unknown or conflicting policy returns no
protected field.

Results carry stable IDs, the document's approved labels, selection status,
coverage state and matched coverage targets, duplicate-cluster references,
evidence scope, per-field provenance edge and current policy identifiers, and
each value's digest and length. Field text itself is not returned by default.
A rights-safe snippet appears only when the current policy explicitly grants
`snippet_render`; otherwise the snippet is `null`.

Ranking is a total order over authorized fields only — matched term count,
then visible field count, then the stable document ID — so repeated queries
return identical ranks. A result limit truncates the page without changing
`result_count`.

## Not leaking through counts, facets, clusters, or timing

Facets are computed only from the results this exact caller is allowed to see.
A caller with no authorized field gets zero results and empty facets for every
dimension, and the response contains no document ID, cluster ID, or value.
Duplicate-cluster membership is filtered the same way: a result lists only the
sibling documents that are themselves visible to that caller, so a cluster
cannot be used to count records behind a policy.

Authority traffic is deliberately answer-independent. Every field of every
structurally matching document has its current grant resolved exactly once
before ranking, whatever the rights outcome is, so neither the number of
authority calls nor the work done reveals which fields were authorized.
Command-line failures collapse to one generic code and next action rather than
reporting which record or which gate refused.

## Score-generation export

`export_score_features` produces a rights-safe export for score generation.
The export is refused outright for a public audience, and every feature must
carry a current policy that grants the `score_generation` operation to the
requesting audience.

An exported feature is stable IDs and structured attributes: field ID, name,
origin class, provenance edge, current visibility policy, rights and consent
snapshots, and the value digest and length. The value text itself is exported
only when the policy also grants `score_feature_value`, and even then a value
longer than the feature cap stops the export rather than shipping long-form
text. Source bytes, full prose, captions, transcripts, personal data, signed
URLs, and machine-local paths have no representation in the export schema at
all.

Exact derived object keys are included only when the binding's retrieval
decision is `approved` and the current receipt still verifies at export time.
The key is an object key, never a signed URL: the export is re-scanned for
URLs, query syntax, local paths, and credential shapes before it is returned.
The export identifier is the digest of its own content, so the same authorized
state exports byte-identically.

## Revocation, withdrawal, expiry, correction, and deletion

Because search and export always re-resolve current authority, a stored index
generation is a cache and never a grant. Revoking rights, withdrawing consent,
letting evidence or policy expire, or correcting the source removes the field
from results, from facets, and from exports immediately, using the same stored
generation and with no reindex required.

An exact deletion event removes the field from the next generation as well.
The rebuilt index reports it under `superseded_fields`, the entry no longer
lists the field, derived bindings that depended on that field's transformation
are gone with it, and the field appears in no result, facet, or export.
Applying the same events again rebuilds byte-identically, so replay is safe.

## Commands

All commands are local and offline. `--authority` is a reviewed authority
bundle (`index_authority_bundle`) holding the current documents, visibility
policies, provenance edges, deletion events, and object receipts.

```bash
python3 -m performing_fire_corpus search build \
  --index-id corpus_index_001 \
  --snapshot local/snapshot.json \
  --authority local/authority.json \
  --built-at 2026-07-24T00:00:00Z \
  --derived-objects local/derived-objects.json \
  --coverage-targets local/coverage-targets.json \
  --output local/index.json

python3 -m performing_fire_corpus search query \
  --index local/index.json \
  --authority local/authority.json \
  --audience researcher \
  --current-time 2026-07-25T00:00:00Z \
  --term synthetic \
  --output local/results.json

python3 -m performing_fire_corpus search export-scores \
  --index local/index.json \
  --authority local/authority.json \
  --audience researcher \
  --current-time 2026-07-25T00:00:00Z \
  --output local/score-export.json
```

Exit codes follow the repository taxonomy: `0` complete, `4` blocked by
missing or refused authority, `1` otherwise.

## Reviewer replay

1. Run `python3 -m unittest tests.test_rights_filtered_search -v`. The
   fixtures are synthetic and checked in; no network, R2, or ledger access is
   involved.
2. Rebuild the same index twice and compare canonical bytes to confirm
   deterministic indexing, then round-trip one generation through JSON to
   confirm restart is identical.
3. Query the same generation as `researcher`, `operator`, and `public` and
   compare result counts, facets, and duplicate members. The public response
   must be empty in every dimension and must not mention any document.
4. Revoke a policy in the authority bundle only — leave the index file
   untouched — and re-run the query and the export to confirm the field
   disappears from results, facets, and the export.
5. Rebuild after an exact deletion event and confirm `superseded_fields`
   names the removed field and that the field is absent everywhere.

## Loopback API

A loopback-only HTTP interface would be a thin wrapper over the same three
functions and is intentionally not implemented here. Nothing in this surface
implies a hosted service, a deployed operator UI, or production
authentication.
