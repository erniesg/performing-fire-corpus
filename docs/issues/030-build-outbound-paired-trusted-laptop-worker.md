# Build the outbound-paired trusted-laptop worker

depends-on: 027,029

## Goal

Implement the later trusted-laptop lane for OCR, transcription, and video understanding as an outbound-paired worker. It consumes approved exact R2 object keys, writes approved derived objects and manifests back to R2, and never transfers source bytes or machine-local paths through queue payloads.

## Acceptance tests

- Define a versioned pairing and capability protocol in which the laptop initiates outbound HTTPS, advertises bounded capabilities, claims expiring leases, heartbeats, checkpoints, and releases on disconnect. No inbound laptop access is required.
- Queue payloads contain stable job, source, asset, rights, transformation, and input object IDs plus exact R2 keys and hashes; reject source bytes, credentials, signed URLs, device identifiers, and machine-local paths.
- Download only an exact approved input key after current derivative rights, privacy, retention, and capability checks; verify input hash and size before processing.
- Write outputs only to immutable derived keys with transformation manifests, input provenance, tool or model version, deterministic parameters where applicable, output hash, redaction state, and inherited rights.
- Remove local input, output, and working cache after success, failure, lease loss, or restart. Persist no corpus bytes in Git, issue comments, evidence, or long-lived laptop state.
- Support interruption and resume without duplicate derived receipts, conflicting outputs, or stale leases. A transformation-version change creates a new derived key rather than overwriting.
- Enforce per-job CPU, memory, disk, elapsed-time, output-byte, retry, and concurrency limits, with a default concurrency of one.
- Add a fully local fake-object-store and fake-pairing harness covering lease expiry, disconnect, input mismatch, output conflict, deletion propagation, redaction, and zero dynamic-transit content leakage.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

Worker pairing and R2 credential names must be introduced only through a separately reviewed trusted-laptop deployment contract. Portable implementation and tests use no real secrets.

## Artifact outputs

- New pairing, capability, transformation-job, and derived-manifest schemas under `schemas/`
- New trusted-laptop worker modules under `src/performing_fire_corpus/`
- New offline object-store, pairing, lease, cache, and redaction tests
- New trusted-laptop lane documentation under `docs/`

## Stop conditions

- Stop if inbound access, a local media path in a payload, source bytes in transit metadata, ambient cloud credentials, or persistent local corpus storage is required.
- Stop if derivative rights, consent, retention, exact input key, hash, or worker capability is incomplete.
- Stop on lease loss, input mismatch, output conflict, resource-bound exhaustion, or deletion obligation.
- Stop if evidence or logs could reveal content, transcripts, personal details, device identity, secrets, signed URLs, or local paths.

## Human clarification protocol

Ask only when choosing a pairing transport or credential boundary is necessary to run the first trusted-laptop job. Present content-free options, recommend outbound HTTPS with short-lived least-privilege credentials, and require a separate reviewed deployment decision.

## Recommended response

Keep the portable worker implementation storage- and transport-injected, use exact-key handoffs and disposable cache, and defer live pairing credentials until one rights-approved derivative job exists.

## Trade-offs

Outbound pairing avoids exposing the laptop but adds lease and credential-lifecycle complexity. Local processing supports private tools while demanding stronger cache deletion and evidence discipline.

## Free-form response

Optional maintainer notes or alternate outbound pairing design:
