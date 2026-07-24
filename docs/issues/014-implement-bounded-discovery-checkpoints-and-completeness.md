# Implement bounded discovery checkpoints and completeness accounting

depends-on: 002,012,013

## Goal

Generalize the one-source acquisition path into a source-neutral metadata discovery engine with hard request, byte, page, retry, and elapsed-time limits; stable resumable pagination; sanitized request facts; and honest completeness accounting.

## Acceptance tests

- Add strict run-plan, page-checkpoint, request-fact, discovery-observation, and completeness-report contracts tied to a canonical source ID, adapter version, normalized endpoint ID, and policy snapshot.
- Require positive limits for total requests, bytes per response, aggregate bytes, pages, elapsed time, per-host interval, retries, and bounded `Retry-After`; no adapter may weaken or omit a run-plan limit.
- Persist a stable opaque pagination checkpoint only after the corresponding sanitized request fact and parsed record upserts commit atomically. Reject checkpoints containing signed URLs, cookies, headers, bodies, account identifiers, or local paths.
- Resume from the last committed checkpoint without duplicating request facts, records, blockers, or work. Detect pagination loops, token reuse, non-monotonic cursors, changed adapter versions, and source-policy expiry.
- Count observed unique records, duplicates, rejected records, blocked pages, terminal pages, expected totals only when a source explicitly supplies them, and the unvisited remainder implied by the active bounds.
- Report completeness as evidence-scoped states such as `complete_for_observed_endpoint`, `bounded_partial`, `blocked`, `changed`, or `unknown`; never infer whole-source completeness from an exhausted page budget.
- Keep request ledgers sanitized and body-free. Parsers receive bounded in-memory bytes, discard them after parsing, and retain only approved factual metadata fields.
- Add fake-clock and fake-transport tests for stable pagination, interruption at every commit boundary, retry exhaustion, rate limiting, loops, shape drift, duplicate records, stale robots or policy, and deterministic completeness reports.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. Portable tests use synthetic page shapes and fake transports.

## Artifact outputs

- New versioned discovery-run and completeness schemas under `schemas/`
- New source-neutral discovery engine under `src/performing_fire_corpus/`
- Durable migration assets for checkpoints and request facts
- New synthetic pagination, resume, budget, and completeness tests

## Stop conditions

- Stop if an adapter can issue a request without a remaining budget, current policy decision, and current robots result where applicable.
- Stop on ambiguous or repeating pagination, changed shape, stale authority, or a checkpoint that cannot be sanitized.
- Stop if any count is presented as whole-source completeness without bounded evidence supporting that scope.
- Stop if response bodies, platform tokens, or machine-local state become durable resume inputs.

## Human clarification protocol

Ask only if a source exposes two supported pagination mechanisms with materially different terms or completeness guarantees and choosing one blocks its adapter. Provide the bounded alternatives, recommend the documented public metadata mechanism, and leave room for another reviewed choice.

## Recommended response

Use a transactionally committed cursor plus an adapter-version and policy-snapshot fingerprint. Report partial completeness explicitly whenever a budget, blocker, or unsupported endpoint ends the run.

## Trade-offs

Atomic checkpoints add ledger writes and may reduce throughput, but make resume auditable. Exact completeness may remain unknown for sources that publish no total, which is more honest than extrapolating a catalogue size.

## Free-form response

Optional maintainer notes or alternate checkpoint strategy:
