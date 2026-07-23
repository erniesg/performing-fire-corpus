# Implement the production R2 S3 adapter and CLI wiring

depends-on: 006,008

## Goal

Implement the concrete Cloudflare R2 S3-compatible storage adapter and wire it into the existing redacted readiness and approved bounded-transfer CLI contracts. Use red/green/refactor TDD, keep all automated tests offline, and preserve the current fail-closed privacy, rights, immutability, and evidence boundaries.

## Acceptance tests

- Write failing adapter and CLI tests before production code, then implement the smallest passing change and refactor without weakening the existing fake-client contract.
- Add one version-bounded Python S3 SDK dependency and a concrete R2 client that receives only the configured bucket, dedicated staging prefix, and the four existing environment secret names. Disable ambient AWS profile, shared-config, and instance-metadata credential discovery; never generate presigned URLs.
- Validate the configured endpoint as HTTPS and R2-specific before constructing the SDK client. Configuration or SDK errors expose only a stable sanitized code and next action, never bucket or prefix values, endpoint values, account identifiers, credential fragments, request headers, signed requests, or provider response bodies.
- Implement `probe_scope` with one prefix-bounded request, `head_object` for one exact key, and conditional single-object creation with `If-None-Match: *`. Do not list buckets, create buckets, mutate bucket policy, enumerate outside the configured prefix, or overwrite an existing key.
- Store and reconstruct the existing receipt facts through S3 object metadata: byte size, normalized media type, and lowercase SHA-256. Treat missing or malformed metadata, ambiguous SDK results, unexpected redirects, `403`, and any non-not-found `HEAD` failure as closed failures.
- Map only a verified not-found response to no object and only a verified precondition failure to “already exists.” After every create attempt, reuse the existing exact-key `HEAD` verification and block on a size, MIME, or hash conflict.
- Wire `performing-fire-corpus r2 readiness` to construct the production adapter when configuration and all required secret names are present. Keep dependency injection for tests and preserve output containing field names plus `present` or `missing` only.
- Add low-level `performing-fire-corpus r2 transfer-approved` CLI wiring that loads one reviewed, schema-validated local approval plan, an explicit ledger, cache directory, and sanitized receipt output; invokes the existing bounded streaming transfer with the production R2 client; and never prints plan values, source content, local paths, or secrets.
- Test the SDK boundary with stubs and fake HTTP streams only, including missing credentials, invalid endpoint, wrong bucket or prefix scope, bounded prefix probe, not-found, conditional-create race, conflicting metadata, upload interruption, redacted exceptions, CLI exit taxonomy, and successful receipt persistence. Patch socket and network entry points so any live DNS, HTTP, or R2 attempt fails the test.
- Keep model or effort racing, multipart transfer, bucket creation, lifecycle policy, broad listing or deletion, and any live proof out of scope.

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

Automated tests use invented values only. Production values may be read only from the trusted-VM secret store by an explicitly invoked CLI command and must never be returned, printed, persisted, or included in test failures.

## Artifact outputs

- Production R2 S3 adapter and explicit client factory under `src/performing_fire_corpus/`
- Readiness and single-approved-transfer CLI wiring under `src/performing_fire_corpus/`
- Version-bounded package metadata update
- Offline SDK-stub, fake-stream, redaction, and CLI tests under `tests/`

## Stop conditions

- Stop if the SDK cannot perform an atomic conditional create for one exact key or cannot distinguish verified not-found and precondition-failed outcomes without inspecting or exposing sensitive response material.
- Stop before constructing a client when any required secret, bucket, dedicated prefix, endpoint validation, rights approval, MIME allowlist, byte bound, or cleanup or retention decision is missing.
- Stop if implementation would use ambient credentials, generate a signed URL, enumerate a bucket or broad prefix, overwrite an object, persist source bytes outside disposable cache, or make a live request in tests.
- Stop if any output, exception, fixture, issue, or evidence could contain source bodies, raw HTML, media, personal information, private documents, local absolute paths, credentials, account identifiers, cookies, signed URLs, or secret values.

## Human clarification protocol

No human setup is required to implement or validate this issue because all tests are offline. If a live adapter check is proposed, do not perform it here and do not request secret values in an issue or chat. Keep the result held for issue 010, report required names as `present` or `missing` only, and ask the maintainer to use the official R2 S3 setup and authentication pages in that issue’s reviewed trusted-VM gate.

## Recommended response

Implement the adapter against SDK stubs, retain the existing `StorageClient` and transfer seams, and leave live credentials and network execution untouched. Defer the one-object operator run and exact-key cleanup proof to issue 010 after its human gate is complete.

## Trade-offs

A version-bounded S3 SDK adds a runtime dependency, but it provides maintained request signing and explicit conditional operations. Single-request uploads keep the proof small and deterministic; multipart and high-throughput behavior can be considered only after the bounded one-object path is proven.

## Free-form response

Optional maintainer notes or an alternate SDK choice:
