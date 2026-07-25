# Outbound-paired trusted-laptop worker

Issue #40 adds the portable contract for the later OCR, transcription, and
video-understanding lane. It does not configure a live laptop, pairing
credential, R2 credential, model, media tool, inbound listener, or hosted
operator surface.

## Boundary

The laptop is always the client. It advertises one strict
`trusted_laptop_capability` record through an injected outbound HTTPS control
plane, receives one expiring pairing, and claims at most one job. The protocol
has no hostname, device identifier, inbound endpoint, cookie, credential,
signed URL, source URL, source bytes, or machine-local path field.

Queue jobs carry:

- stable job, source, asset, rights, receipt, and transformation identifiers;
- one exact input object key, hash, size, and MIME type;
- current rights, privacy, retention, and derivation-authority snapshot hashes;
- a version-bound tool contract and a small allowlisted parameter object; and
- explicit CPU, memory, disk, elapsed-time, input-byte, output-byte, retry, and
  capability limits.

The output object key is not guessed before content exists. The worker hashes
the bounded output, derives the exact immutable key from the source, asset,
version-bound transformation ID, and output hash, then checkpoints those facts
before attempting a create. Queue and checkpoint transit therefore carries
identifiers and exact object keys, never media or local paths.

## Fail-closed order

For each claim, the worker:

1. validates the current pairing, capability, job, retry budget, and lease;
2. resolves current derivative-rights, consent, privacy, retention, deletion,
   and capability authority;
3. verifies the durable input receipt and exact-key `HEAD`;
4. downloads only that approved key into a marker-bound disposable cache and
   verifies its exact hash and size before the transformer runs;
5. supervises a storage- and tool-injected transformation under the job
   resource limits;
6. checkpoints the exact output hash, size, key, and aggregate resource facts
   before any object create;
7. refreshes the lease, elapsed bound, current authority, input tombstone, and
   target tombstone at the actual conditional-create call;
8. accepts an output only after conditional create or matching exact-key
   recovery, exact-key `HEAD`, and a durable object receipt;
9. builds the existing most-restrictive derivation manifest from verified
   receipts, then applies the same guarded immutable-create sequence to the
   manifest; and
10. completes with a content-free result containing exact object/receipt IDs,
    hashes, sizes, inherited rights/privacy/retention facts, and aggregate
    resource use.

There is no list operation and no object delete operation in this worker.
Deletion propagation is checked before input access, after transformation,
before each actual create, and again while resuming a durable receipt. A
tombstone or withdrawn authority blocks the affected job and descendants.

## Crash and disconnect recovery

Checkpoints are hash-bound and monotonic by stage:

- `transform_verified` fixes the only output hash/key the job may create;
- `output_verified` fixes the durable derived-object receipt; and
- `manifest_verified` fixes both exact object receipts.

If a process stops after transform but before receipt, the same transformation
may be rerun only to reproduce the checkpointed hash. A different output is a
durable blocker and cannot create a second key. If the output receipt is
already durable, resume skips download and transformation. If both receipts
are durable, resume verifies exact `HEAD` facts and completes without reading
corpus bytes.

A lost conditional-create response is accepted only when immediate exact-key
`HEAD` matches the declared size, MIME type, and hash. Lease or outbound
pairing loss releases only that job. It does not hold unrelated work.

## Disposable cache

The cache root is an injected trusted-laptop setting and is not serialized.
Each job directory has a content-free ownership marker bound to its pairing,
lease, and job. Normal success and every handled failure remove that exact
directory. Startup reaping removes only worker-named, validly marker-bound
directories whose lease has expired. Symlinks, malformed markers, active
leases, and unrelated paths are preserved.

The transformer and exact-object-store adapters receive local paths only as
in-process arguments. They must enforce their declared streaming and operating
system limits; they may not put paths or bytes in checkpoints, logs, errors,
issues, evidence, or manifests.

## Durable records

`schemas/v1/trusted-laptop-worker.json` publishes strict version-1 schemas for:

- capability advertisement;
- outbound pairing;
- transformation jobs;
- leases and heartbeats;
- resumable checkpoints;
- successful results; and
- sanitized blockers.

Derived-object and derivation-manifest records continue to use
`schemas/v1/derived-object.json`,
`schemas/v1/derivation-manifest.json`, and the immutable namespaces documented
in `docs/full-corpus-object-storage.md`.

## Human authority

The portable implementation needs no human input. A future live pairing must
separately review outbound HTTPS transport, short-lived least-privilege
credentials, the laptop cache root, the concrete tool sandbox, and the first
asset-specific derivative authority. Until then, there is no claim that a
trusted laptop or hosted pairing UI is deployed.

Rights, consent, privacy, retention, deletion, immutable-object conflicts, and
nondeterministic-resume conflicts require corpus-operator authority. Provider
disconnects, temporary resolver failures, lease loss, and ordinary capacity
shortfalls remain resumable non-human blockers.

## Offline verification

```bash
python3 -m unittest tests.test_trusted_laptop_worker -v
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

The fake pairing, object store, authority resolver, receipt ledger,
transformer, and clock use only synthetic bytes and make no network or secret
request.
