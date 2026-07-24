# Full-corpus object-storage contract

This contract governs the deliberately selected corpus after the bounded
one-object pipeline proof. It does not widen that proof, grant source rights,
or turn the metadata inventory into a bulk mirror.

No production operation is authorized by this document or by the portable
implementation. Portable tests use fake storage only.

## Immutable namespaces

All object keys use a reviewed dedicated prefix followed by one of these
versioned shapes:

```text
PREFIX/v1/raw/SOURCE_ID/ASSET_ID/SHA256
PREFIX/v1/derived/SOURCE_ID/ASSET_ID/TRANSFORMATION_ID/SHA256
PREFIX/v1/manifests/SOURCE_ID/ASSET_ID/MANIFEST_ID/SHA256
PREFIX/v1/tombstones/SOURCE_ID/ASSET_ID/TOMBSTONE_ID/SHA256
```

The `v1/raw/`, `v1/derived/`, `v1/manifests/`, and `v1/tombstones/`
namespaces accept only normalized stable identifiers and lowercase SHA-256
digests. Titles, prose, personal details, account or provider identifiers,
credentials, cookies, signed values, endpoints, and machine-local paths are
not key components or durable metadata. Object keys never use a public URL or
a filename.

Workers receive one exact key as a capability. They must not list a bucket or
prefix, overwrite a key, or infer a broader selector. Worker and proof paths
must not delete a bucket or prefix.

## Immutable create and verification

A raw, derived, or manifest object is eligible for a verified receipt only
after all of these facts agree:

1. The bounded local file has the declared byte size and SHA-256.
2. A conditional create reports `created`, reports `already exists`, or loses
   its response.
3. A follow-up exact-key `HEAD` matches byte size, normalized MIME type, and
   SHA-256.
4. The receipt names the stable source and asset IDs, rights-snapshot hash,
   retention class, creation-run ID, retrieval decision, and sanitized
   evidence reference.

A matching object may be reused without another create. Every create attempt
receives the durable corpus ledger as its receipt authority. A terminal rerun
returns the already committed `created` receipt rather than manufacturing a
conflicting `reused` receipt. A matching pre-existing object with no durable
created receipt remains reused and has no same-proof deletion authority. A lost create response
is treated as a verified but non-owned
`reused_after_ambiguous_create` only when the immediate exact-key `HEAD`
matches every immutable fact. It is never same-proof deletion authority. An
absent or conflicting object is held; a receipt never becomes verified merely
because a request was attempted.

The receipt ID binds every immutable receipt fact, including creation run,
create disposition, size, MIME type, rights snapshot, retention class,
retrieval decision, and evidence reference. Changing any fact without
re-binding the ID fails validation.

## Receipts and crash reconciliation

The object receipt, sanitized receipt artifact, and ledger entry are separate
durable boundaries. Reconciliation is read-only until exact-key verification
succeeds:

| Receipt artifact | Ledger entry | Safe next action |
| --- | --- | --- |
| absent | absent | `write_receipt_then_ledger` |
| present | absent | `write_ledger_from_receipt` |
| absent | present | `write_receipt_from_ledger` |
| present | present | `complete` |

Both durable copies must equal the expected verified receipt. A mismatched
copy, missing object, or conflicting exact-key `HEAD` produces a durable hold.
Reconciliation never creates another object and never lists storage.
The repository ledger accepts strict `object_receipt` records, runs the full
content-binding validator at insertion, applies the existing approved-rights
gate, and enforces one durable receipt per exact key. Full-corpus receipts also
satisfy the raw/derived object-store asset-state gates; a parallel legacy
receipt is neither required nor permitted for the same key.

## Exact-content deduplication and provenance

Deduplication groups only identical lowercase content hashes. Each
source/asset/rights-snapshot edge remains separate, including cross-source
duplicates. The effective retrieval decision is the most restrictive decision
in the cluster:

```text
approved < metadata_only < blocked
```

An approved edge does not relax another source's block, retention class,
consent, deletion obligation, or downstream-use restriction. Derivation
manifests compute the effective decision from all input receipts and require
every output to use a rights snapshot carrying that most restrictive decision.
When equally restrictive inputs have distinct rights snapshots, the output
uses the deterministic combined-snapshot hash; choosing only one input snapshot
is not valid inheritance.

## Raw-to-derived manifests

A version-1 derivation manifest binds:

- the transformation, tool, tool version, and contract version;
- deterministic JSON parameters from a reviewed field allowlist and their
  canonical SHA-256;
- verified input and output receipt IDs, exact object keys, and hashes;
- input and output rights-snapshot hashes with `most_restrictive`
  inheritance;
- the redaction state and a sanitized evidence reference;
- a manifest hash binding all transformation, receipt, and rights facts.

Queue messages between the trusted VM, R2, and a later outbound-paired trusted
laptop carry these identifiers and object keys, never machine-local media
paths. OCR, transcription, and video-understanding tools cannot silently
replace or weaken the input authority.

A complete derivation-lineage snapshot is rebuilt from every receipt and
manifest in the durable corpus ledger. It verifies each key, content hash,
rights snapshot, retrieval decision, and output receipt against its owning
manifest, rejects disconnected or multiply owned descendants, and hash-binds
the complete authoritative graph. Caller-supplied subsets cannot declare
themselves complete. Retention targets are derived from that snapshot; callers
cannot add an unrelated same-asset object or omit a known descendant.

## Retention, legal holds, and exact cleanup

Retention work lists each exact raw and derived key. Derived targets are
propagated from the same source and asset; unrelated keys cannot be added.
States are:

- `not_due` before expiry;
- `awaiting_review` for ordinary corpus deletion;
- `legal_hold_conflict` while a legal hold is active;
- `ready_exact_cleanup` only for a narrowly authorized disposable proof.

The only executable portable cleanup authority is
`same_proof_disposable`. It requires every target receipt to identify the same
creation run, the same retention class, and a create disposition of `created`.
It never authorizes deletion of a reused or pre-existing object.
Ordinary corpus data remains `held_for_review`, even after retention expiry.

Exact cleanup verifies the current object metadata before deletion, deletes
only the named key, confirms that exact key is absent, and emits a deterministic
tombstone. An already absent exact key is an idempotent tombstone state. A
provider error or conflicting object becomes `failed_cleanup`; the worker does
not broaden scope or include the provider response in evidence.

Every retention work item binds both a complete lineage hash and a current retention/legal-hold authority
hash. That authority has a bounded validity
window and normalized whole-second UTC timestamps. Both records are
revalidated immediately before deletion. The durable ledger holds an immediate
write guard across that validation and the exact-key operations, so a new
receipt or manifest cannot race a supposedly complete cleanup snapshot. Each
target is resolved again by exact key from that guarded ledger and must equal
the work receipt. A newly active hold, expired
authority, changed retention decision, or changed lineage stops cleanup and
requires rebuilt work.

## Production boundary

The implementation in `performing_fire_corpus.corpus_objects` is a portable
contract and fake-storage conformance surface. A future production caller
still requires:

- current operation-specific rights and retention authority;
- the trusted-worker capability and reviewed per-run scope;
- required secret names present without exposing values;
- a dedicated bucket/prefix match;
- exact-key receipts outside Git;
- pre/post trusted-VM verification;
- the existing cost, privacy, and human-authority gates.

Live source requests, downloads, R2 creates, normal-corpus deletion, and legal
hold decisions remain outside this issue.
