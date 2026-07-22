# Inventory one source with bounded public metadata requests

depends-on: 002,003,004

## Goal

Implement the first `network-acquisition` command that verifies and inventories metadata for one checked-in public source using bounded requests, robots-aware behavior, sanitized request evidence, and no committed response bodies.

## Acceptance tests

- Add a source-adapter interface and implement exactly one adapter selected from the public source URLs already in `docs/PROJECT_BRIEF.md`; do not encode unverified catalogue counts or undocumented API claims as facts.
- Add a CLI command with explicit source, maximum-request, timeout, rate-limit, retry, ledger, and sanitized-manifest parameters. Defaults are conservative and every run has a hard request and elapsed-time bound.
- Consult applicable robots metadata before catalogue requests, use only public unauthenticated `GET` or `HEAD` requests, and apply the centralized allowlist, redirect, rate-limit, retry, and redaction policies.
- Parse only the minimal metadata fields needed for source and asset records in memory. Never write raw HTML, JSON response bodies, article prose, images, audio, video, captions, transcripts, or embeddings to disk, logs, fixtures, or evidence.
- Record sanitized request facts including public URL, status, MIME type, declared or observed byte count, timestamp, retry outcome, and response hash when safe; no headers or query values that may carry credentials are retained.
- A `403`, robots denial, rate limit beyond the retry budget, login requirement, unexpected MIME type, oversized response, or changed source structure writes a durable blocked result with the next safe action and exits without bypassing the restriction.
- Repeating or resuming a run upserts stable records and does not duplicate assets, jobs, requests, or blockers.
- Unit and integration tests use fake transports and captured synthetic metadata shapes only; an opt-in trusted-VM smoke command is documented separately and is not part of portable CI.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

None. The live command is limited to unauthenticated public metadata. If authentication is requested, record a blocker instead.

## Artifact outputs

- Source adapter and bounded HTTP transport integration under `src/performing_fire_corpus/`
- Sanitized request-ledger and manifest records
- Fake-transport tests and a documented opt-in trusted-VM smoke command

## Stop conditions

- Stop on robots denial, `403`, login or subscription requirement, unapproved redirect host, repeated rate limiting, changed source shape, non-metadata content, or a response exceeding the configured bound.
- Stop if implementation would persist a response body or copyrighted source content.
- Stop if the selected source cannot be inventoried without browser authentication or access-control bypass.

## Human clarification protocol

Ask only if every checked-in source is blocked for metadata-only access or if choosing a different source changes the next executable adapter task. Report sanitized request facts, recommend the least restrictive public metadata source, and leave room for another source choice.

## Recommended response

Prefer the source that exposes the clearest unauthenticated metadata and robots behavior under the smallest request budget. If none does, preserve the blockers and keep fixture discovery as the proven slice.

## Trade-offs

One adapter does not establish full-corpus coverage, but it tests the acquisition boundary without multiplying source-specific assumptions. Strict body and request limits may reduce metadata completeness while protecting rights and evidence hygiene.

## Free-form response

Optional maintainer notes or an alternate source choice:

