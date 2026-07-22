# Run the first metadata-only readiness proof

depends-on: 005

## Goal

Run and document the first trusted-VM, metadata-only proof for one public source, demonstrating bounded discovery, durable restart behavior, privacy-safe evidence, and an honest readiness matrix before any corpus object is downloaded or uploaded.

## Acceptance tests

- Run the documented bounded metadata command from a clean checkout with an explicit request cap, timeout, rate limit, temporary durable ledger, and sanitized manifest destination.
- Record the source selected, public URLs requested, robots observation, statuses, MIME types, byte counts, response hashes when safe, retry outcomes, discovered record counts, blockers, and next safe action without retaining response bodies.
- Interrupt after at least one durable checkpoint when the source shape permits, rerun against the same ledger, and show that assets, jobs, requests, and blockers are not duplicated.
- Run `scripts/agent-evidence` and retain only its sanitized manifest and logs under the repository evidence policy; do not add generated evidence or source-derived material to Git.
- Produce a concise gap matrix mapping every first-usable-slice promise in `README.md` and `docs/PROJECT_BRIEF.md` to a passing test, proof artifact, explicit blocker, or follow-up issue.
- Verify the proof performs no media, document, caption, transcript, embedding, or R2 transfer and uses no credentials.
- Treat `403`, robots denial, changed structure, oversized metadata, or exhausted retries as a valid durable blocked proof when the evidence and next safe action are complete.

## Validation command

```bash
scripts/agent-evidence
```

## Allowed secrets

None. The proof uses only unauthenticated public metadata requests.

## Artifact outputs

- Sanitized trusted-VM evidence manifest and request ledger outside normal Git commits
- Deterministic metadata manifest and restart comparison
- First-usable-slice gap matrix containing only public metadata and repository facts

## Stop conditions

- Stop on any access-control bypass request, login requirement, unbounded response, source content persistence, secret exposure, or privacy-boundary failure.
- Stop before downloading or uploading any corpus object.
- Stop if the current checkout cannot be tied to an exact commit for evidence.

## Human clarification protocol

Ask only if all allowed sources produce blockers that do not identify a safe next adapter change, or if evidence cannot be kept free of source content. Provide the sanitized blocker set, recommend preserving the failed-closed result, and leave room for an alternate public source or evidence method.

## Recommended response

Accept a fully evidenced blocked metadata run as a valid proof of fail-closed behavior, then open the smallest source-adapter follow-up supported by sanitized facts. Do not proceed to an object transfer until the metadata proof and gap matrix are reviewed.

## Trade-offs

A single-source proof cannot validate total corpus size or every adapter. It gives a falsifiable readiness signal while keeping the first external interaction narrow and reversible.

## Free-form response

Optional maintainer notes or an alternate proof decision:

