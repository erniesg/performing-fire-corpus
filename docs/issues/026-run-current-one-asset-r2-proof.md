# Run the current one-asset R2 proof

depends-on: 010,025

## Goal

Execute the existing trusted-VM one-object command only within the fresh approval from issue 025. Verify upload, immutable exact-key `HEAD`, and exact-key deletion or reviewed retention with sanitized, resumable receipts and no broader storage or source access.

## Acceptance tests

- Run `infra/vm/verify.sh` immediately before and after the proof and tie the run to an exact clean commit and unexpired approval window.
- Revalidate approval, exact public host and URL, current robots rules, rights and platform basis, MIME, byte bound, dedicated bucket and prefix, storage scope, cleanup or retention, and all required secret-name presence before source acquisition.
- Make at most the documented robots request plus one bounded asset request and the exact storage operations required by the approval. Follow no unapproved redirect.
- Stream through disposable cache, hash while bounded, upload to one immutable content-addressed key, and verify the same exact key by `HEAD` against asset ID, bytes, MIME, and SHA-256.
- For deletion, remove only the verified exact key and verify it absent. For reviewed retention, emit the retention expiry and deletion owner without listing the bucket or prefix.
- Persist atomic sanitized readiness, request, upload-attempt, object, verification, cleanup or retention, and run-manifest receipts. Resume cleanup only from matching durable exact-key receipts.
- Remove every cache file after success or failure. A terminal rerun makes no public or storage request.
- Keep approval, ledgers, cache, receipts, logs, and provider details outside Git. Commit only sanitized aggregate proof status and tests, if changed.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

On the approved trusted VM only:

```bash
infra/vm/verify.sh
PYTHONPATH=src python3 -m performing_fire_corpus trusted-vm acquire-one-to-r2 --approval .local/r2-proof/approval.json --database .local/r2-proof/ledger.sqlite3 --storage-config .agent/storage.yaml --cache-directory .local/r2-proof/cache --sanitized-output .local/r2-proof/receipts
infra/vm/verify.sh
```

## Allowed secrets

- `CLOUDFLARE_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ENDPOINT`

Values remain in the trusted-VM secret store and never enter command arguments, output, receipts, issues, evidence, or commits.

## Artifact outputs

- Ignored approval, durable ledger, cache lifecycle, and sanitized receipts under `.local/r2-proof/`
- At most one temporary or reviewed retained immutable proof object
- Exact-key verification and cleanup or retention receipt
- Sanitized aggregate proof result and exact next safe action

## Stop conditions

- Stop before network if any current approval, policy, time window, secret-name, scope, or retention gate is incomplete.
- Stop on robots denial or ambiguity, `401`, `403`, login, redirect mismatch, rate or retry exhaustion, MIME or size mismatch, upload ambiguity, object conflict, or verification failure.
- Stop deletion unless matching durable receipts prove the exact key was verified in this run. Never list or broadly delete.
- Stop if any secret, source byte, provider detail, account identifier, endpoint value, signed request, or local path could enter published evidence.

## Human clarification protocol

Ask only if the approved run reaches a durable ambiguous storage state that the existing exact-key receipts cannot safely resolve. Report the stable sanitized outcome code, recommend no further mutation, and request a reviewed exact-key recovery decision.

## Recommended response

Run once inside the narrow approval. Accept a fail-closed blocker as valid proof evidence, and do not broaden the source, time, byte, or storage scope to force success.

## Trade-offs

The proof may delete its only object immediately, limiting durability evidence, but validates the high-risk transit path reversibly. Exact-key-only recovery can require human review when a provider response is ambiguous.

## Free-form response

Optional maintainer notes about the sanitized proof outcome:
