# Run the bounded selected rich-corpus pilot

depends-on: 024,027,028,029,031,033,034,037,038

## Goal

Run a small, deliberately selected, rights-approved end-to-end pilot from metadata inventory through exact-key raw storage, approved derivation, indexing, rights-filtered search, and score-generation export. This validates the corpus workflow without bulk mirroring or redefining the known source universe.

## Acceptance tests

- Freeze a versioned pilot selection manifest with a small explicit asset count, source and coverage rationale, duplicate handling, current operation-specific rights, retention, transformation eligibility, and exclusion list.
- Require every selected asset to pass current robots, platform, access, exact URL, MIME, byte, storage, derivative, index, retrieval, and score-generation gates before any job is queued.
- Use trusted-VM single-asset jobs and immutable raw keys, then outbound-paired trusted-laptop jobs only for explicitly approved OCR, transcription, or video-understanding profiles.
- Verify every raw and derived key by exact `HEAD`, preserve manifests and hashes, perform no bucket or broad prefix listing, and execute only exact-key retention or deletion work.
- Build a deterministic index snapshot and run reviewed search cases proving provenance and rights filters. Produce a content-safe score-generation export of stable IDs and approved structured features only.
- Demonstrate interruption and resume in acquisition and one derived job without duplicate requests, objects, transformations, index records, or receipts.
- Report selected coverage, inventory gaps, blocked candidates, duplicates, metadata and derived quality, deletion readiness, search behavior, and evidence scope without extrapolating whole-source completeness.
- Keep source bytes, derived content, live ledgers, cache, receipts, provider facts, credentials, and private data outside Git. Commit only sanitized aggregate pilot evidence, code, tests, and docs.
- Limit the pilot to the approved manifest. Success does not authorize another asset, source, transformation, retention period, or audience.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

Run live pilot commands only on their reviewed trusted-VM or outbound-paired trusted-laptop lanes after all per-asset gates pass.

## Allowed secrets

Only secret names already authorized by the exact trusted-VM, object-storage, and trusted-laptop run plans. Values remain in scoped secret stores and never enter manifests, queue payloads, evidence, issues, or Git.

## Artifact outputs

- Versioned sanitized pilot selection manifest
- Ignored raw and derived object receipts, worker ledgers, and exact-key manifests
- Deterministic local index snapshot and rights-filtered replay evidence
- Sanitized aggregate pilot evaluation and next-task recommendation under `docs/`

## Stop conditions

- Stop the affected asset on any robots, platform, rights, access, privacy, consent, retention, MIME, byte, storage, transformation, index, or audience ambiguity.
- Stop the whole pilot on redaction, secret-scan, exact-key verification, deletion propagation, or evidence-integrity failure.
- Stop before adding an unselected asset, increasing a bound, enabling bulk concurrency, listing storage, or broadening retrieval.
- Stop if any source or derived content, private material, credential, provider detail, or local path would be committed or published.

## Human clarification protocol

Ask only if one pilot-wide decision blocks the next exact job after all independent work is exhausted. Provide the stable selected asset and gate IDs, current checkpoint, sanitized options, and recommend the narrower reversible action.

## Recommended response

Run a very small stratified pilot with concurrency one, exact-key operations, local rights-filtered search, and deletion rehearsals. Treat blocked assets as coverage findings and do not replace them merely with easier downloads.

## Trade-offs

A small pilot cannot establish production throughput or full corpus quality, but exercises the highest-risk boundaries with reviewable evidence. Rights-safe selection may reduce source balance until permissions improve.

## Free-form response

Optional maintainer notes or alternate bounded pilot size:
