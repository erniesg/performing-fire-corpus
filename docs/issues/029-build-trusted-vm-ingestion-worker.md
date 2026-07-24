# Build the trusted-VM ingestion worker

depends-on: 014,024,027,028

## Goal

Implement a supervised outbound trusted-VM worker that claims only approved acquisition jobs, downloads one bounded asset at a time, verifies it, hands it to immutable R2 storage, and checkpoints durable state. Queue payloads carry stable IDs and exact R2 keys, never machine-local paths or source bytes.

## Acceptance tests

- Define versioned worker capability, acquisition-job, lease, heartbeat, checkpoint, result, and blocker contracts linked to stable source, asset, rights, selection, run-plan, and evidence IDs.
- Claim a job only when its policy snapshot is current and every operation-specific rights, robots, access, MIME, byte, retention, storage-scope, and worker-capability gate is approved.
- Fetch at most one asset per job through bounded disposable cache, enforce final URL, MIME, declared and observed bytes, hash, retries, rate, and elapsed time, and remove cache after every outcome.
- Upload only to the reviewed immutable raw namespace, verify by exact-key `HEAD`, persist object and provenance receipts, and enqueue downstream work using stable IDs and exact object keys.
- Support lease expiry, heartbeat, disconnect release, restart, partial download cleanup, lost upload response, exact-key recovery, duplicate jobs, and terminal idempotent resume without duplicate source or object requests.
- Persist actionable blockers with stable outcome code, affected gate, exact safe next action, resumable checkpoint, and required human authority class.
- Emit sanitized metrics and evidence only. Logs and dynamic transit reject bodies, source bytes, credentials, signed URLs, cookies, headers, account identifiers, private text, and local paths.
- Add offline fake-HTTP, fake-storage, fake-clock, and crash-boundary tests. No portable test performs live network or storage operations.

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

Values may be read only by an explicitly invoked trusted-VM worker for approved jobs. Source-specific secrets require a separate reviewed issue and are not implicitly allowed here.

## Artifact outputs

- New worker, lease, checkpoint, and blocker schemas under `schemas/`
- New trusted-VM worker implementation under `src/performing_fire_corpus/`
- New offline crash, resume, bounds, redaction, and exact-key tests
- New trusted-VM operator documentation under `docs/`

## Stop conditions

- Stop before acquisition when any current policy, selection, rights, robots, access, MIME, byte, retention, scope, secret-name, or capability gate is incomplete.
- Stop on `401`, `403`, login, subscription, disallowed redirect, rate exhaustion, content mismatch, object conflict, verification ambiguity, or lease loss.
- Stop if a queue payload or durable checkpoint contains source bytes, a machine-local path, signed locator, cookie, credential, or provider detail.
- Stop if worker correctness depends on VM disk surviving restart.

## Human clarification protocol

Ask only when one durable job blocker requires authority that cannot be inferred from its reviewed records. Report stable IDs, outcome code, resumable state, and exact safe action; recommend leaving the job blocked without holding unrelated jobs.

## Recommended response

Use single-asset leased jobs, exact object-key handoff, disposable cache, and fail-closed policy snapshots. Keep concurrency at one until crash and rate behavior is evidenced.

## Trade-offs

One-at-a-time ingestion limits throughput but makes rights, bounds, and recovery auditable. Durable checkpoints add storage overhead while preventing retries from becoming duplicate acquisition.

## Free-form response

Optional maintainer notes or alternate worker limit:
