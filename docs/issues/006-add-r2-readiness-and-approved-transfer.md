# Add fail-closed R2 readiness and approved bounded transfer

depends-on: 001,002,003

## Goal

Implement the `object-storage` boundary: a redacted R2 readiness check and an idempotent transfer operation that can upload only an explicitly approved, bounded public asset to an immutable staging key and record a verified receipt.

## Acceptance tests

- Add an `r2 readiness` command that validates configured bucket and staging prefix plus the presence of the four secret names already declared in `.agent/storage.yaml`; output names and `present` or `missing` only, never values or account identifiers.
- Readiness fails closed with a durable next action when configuration or any required secret is absent, and succeeds deterministically with a fake environment and storage client.
- Add a transfer planner that requires an `approved` rights record, expected public source URL, media-type allowlist, maximum byte size, staging prefix, and reviewed retention or cleanup decision before any download or upload begins.
- Stream an approved asset through bounded temporary cache, compute SHA-256 while transferring, reject size or media-type mismatches, and remove partial cache files after success or failure.
- Derive immutable object keys from schema version, stable asset identifier, and content hash; never place source titles, personal information, credentials, signed URLs, or local paths in keys.
- Make upload retry and resume idempotent: an existing matching key and receipt is reused, while a conflicting size or hash blocks without overwrite.
- Record an object receipt with key, byte size, media type, SHA-256, source asset identifier, attempt state, and sanitized evidence reference. Logs never contain secret values, signed requests, response bodies, or media.
- Tests use fake HTTP and R2 clients and cover missing configuration, rights denial, size bounds, MIME mismatch, interrupted stream, retry, matching-object reuse, hash conflict, cleanup, and redaction. No live upload runs in CI.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

- `CLOUDFLARE_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ENDPOINT`

Values may be read only by the trusted-VM storage client during an explicitly authorized proof. They must never appear in output, logs, evidence, fixtures, exceptions, issues, or commits.

## Artifact outputs

- R2 readiness, transfer planning, and storage client modules under `src/performing_fire_corpus/`
- Immutable object receipt records in the durable ledger
- Fake-client tests and a documented held live-proof command

## Stop conditions

- Stop before network access if rights are not `approved`, retention or cleanup is undecided, configuration is incomplete, bounds are missing, or the staging prefix is not dedicated.
- Stop on hash, size, media type, or existing-object conflict; never overwrite or weaken verification.
- Stop if secret values, signed requests, content bytes, or private material could enter output or evidence.

## Human clarification protocol

Ask only when a live proof is requested but the asset approval, byte bound, dedicated staging prefix, or retention and cleanup decision is missing. Report secret names and presence only, recommend a reversible held proof, and leave room for an alternate reviewed decision.

## Recommended response

Implement and test the boundary with fakes first. Keep the live command held until the metadata proof passes and a maintainer selects one small approved public object, a dedicated staging prefix, a byte limit, and a cleanup deadline.

## Trade-offs

Content-addressed immutable keys consume a new key when content changes, but make receipts reproducible and prevent silent overwrite. Streaming adds implementation complexity while keeping cache bounded and disposable.

## Free-form response

Optional maintainer notes or an alternate storage decision:

