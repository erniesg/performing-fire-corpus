# Implement the durable ledger, leases, and progress reconstruction

depends-on: 001

## Goal

Implement a local durable queue and manifest ledger that enforces the corpus state machine, survives process restarts, and supports capability-scoped workers through expiring outbound leases. GitHub issues and PRs remain the canonical work ledger; this database records reproducible corpus execution state.

## Acceptance tests

- Store validated source, asset, rights, job, lease, object, and evidence records in a repository-independent SQLite database selected explicitly by the caller and never committed.
- Enforce forward state transitions from discovery through indexing plus `blocked`, `failed_retryable`, and `failed_final`; reject skips that bypass rights approval or object verification.
- Make repeated upserts, job creation, checkpoint writes, and completion calls idempotent by stable record and operation identifiers.
- Claim jobs only when the worker advertises every required bounded capability; lease claims are atomic, expire at a recorded UTC time, and cannot be completed by a different or expired lease.
- Support heartbeat, checkpoint, retry accounting, disconnect release, and expired-lease recovery without duplicate jobs, downloads, or object receipts.
- Require object keys rather than local paths for jobs crossing the `trusted-vm`, `trusted-laptop`, and `object-storage` lanes; queue payloads contain identifiers and metadata only.
- Add a `progress` CLI command that reconstructs counts by state, retry status, current blockers, active or expired leases, evidence links, issue or PR links, and the next safe action after closing and reopening the database.
- Add red/green tests for every legal and illegal transition, concurrent claim attempts, lease expiry, heartbeat, checkpoint resume, retry exhaustion, duplicate operations, and restart reconstruction.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

None. Tests use temporary SQLite databases and synthetic metadata only.

## Artifact outputs

- Durable ledger and queue modules under `src/performing_fire_corpus/`
- Ledger migration or initialization assets under a versioned package directory
- CLI progress output and synthetic unit tests

## Stop conditions

- Stop if a transition can reach transfer or storage without `approved` rights.
- Stop if correctness depends on machine-local cache surviving a restart.
- Stop if job payloads contain media, credentials, private text, or local absolute paths.
- Stop if SQLite cannot provide an atomic claim boundary for the proposed lease operation.

## Human clarification protocol

Ask only if a destructive migration is unavoidable or two state transitions imply different rights guarantees. Include the affected states, a reversible migration recommendation, and room for a different response.

## Recommended response

Use SQLite transactions and uniqueness constraints as the local durable queue, with UTC timestamps and compare-and-set lease updates. Treat expired leases as recoverable work and preserve the last sanitized checkpoint.

## Trade-offs

SQLite is not a distributed queue, but it provides a deterministic local proof with strong restart behavior. A later orchestration service can consume the same record contracts after the local queue is proven.

## Free-form response

Optional maintainer notes or an alternate ledger decision:

