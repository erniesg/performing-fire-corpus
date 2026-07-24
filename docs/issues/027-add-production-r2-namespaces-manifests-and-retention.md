# Add production R2 namespaces, manifests, deduplication, and retention

depends-on: 006,013

## Goal

Define and implement the full-corpus object-storage contract after the proof: immutable raw and derived namespaces, exact keys, hashes, manifests, deduplication, provenance, retention and cleanup, exact-key verification, and auditable receipts. Keep broad listing and deletion outside all proof and worker paths.

## Acceptance tests

- Define versioned namespaces for raw source objects, derived OCR or transcript or video-understanding objects, manifests, and tombstones. Keys contain schema version, stable source or asset ID, transformation ID where applicable, and lowercase content hash.
- Prevent titles, prose, personal details, credentials, signed values, provider or account identifiers, and machine-local paths from entering keys or metadata.
- Require immutable conditional create, exact-key `HEAD`, matching byte size, MIME, SHA-256, source provenance, rights snapshot, retention class, and receipt before a ledger object becomes verified.
- Deduplicate exact content by hash without collapsing distinct provenance or rights. Cross-source duplicates retain separate asset relationships and the most restrictive applicable retrieval decision.
- Define raw-to-derived manifests with tool and contract version, input object keys and hashes, deterministic parameters, output keys and hashes, rights inheritance, redaction state, and sanitized evidence reference.
- Define retention expiry, exact-key deletion work, derived-deletion propagation, legal-hold conflict, tombstone, and failed-cleanup states. No automatic broad prefix or bucket deletion exists.
- Verify retries and crash recovery for lost create responses, matching existing objects, conflicts, receipt-before-ledger and ledger-before-receipt boundaries, and idempotent exact-key cleanup.
- Use fake storage tests only. Production operations remain held behind per-run approval and trusted-worker capability checks.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

- `CLOUDFLARE_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ENDPOINT`

Portable tests use invented values. Live values remain available only to explicitly approved trusted workers and never appear in artifacts.

## Artifact outputs

- New raw, derived, manifest, receipt, retention, and tombstone schemas under `schemas/`
- New namespace, deduplication, provenance, and retention logic under `src/performing_fire_corpus/`
- New fake-storage crash, conflict, and cleanup tests
- New full-corpus R2 contract documentation under `docs/`

## Stop conditions

- Stop if correctness requires bucket or broad prefix listing, overwrite, or broad deletion.
- Stop if deduplication loses source-specific provenance, consent, rights, or deletion obligations.
- Stop if a derived object can outlive revoked input authority without an explicit reviewed legal-hold decision.
- Stop if secrets, signed requests, content bytes, provider details, or local paths enter receipts or evidence.

## Human clarification protocol

Ask only if a retention conflict between two current approved policies blocks creation or deletion of one exact object. Present content-free stable IDs and deadlines, recommend the shorter or more restrictive policy, and provide room for a reviewed alternative.

## Recommended response

Use content-addressed immutable keys, exact-key verification, separate provenance edges, and deletion propagation. Treat object listing as an administrative audit lane, never a worker or proof dependency.

## Trade-offs

Content-addressed namespaces and provenance edges use more metadata but make deduplication and rights audits reproducible. Exact-key-only operations reduce operational convenience while sharply limiting accidental scope.

## Free-form response

Optional maintainer notes or alternate namespace rule:
