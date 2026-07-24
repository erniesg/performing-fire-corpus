# Run bounded NJP Center main-site and Video Archive inventories

depends-on: 018

## Goal

Run current trusted-VM metadata-only inventories for the NJP Center main site and Video Archive as two independently resumable source proofs. Produce sanitized completeness and blocker accounting without requesting attachment bytes.

## Acceptance tests

- Use separate run plans, ledgers, policy snapshots, checkpoints, and completeness reports for the two canonical source IDs, each with hard request, page, byte, retry, rate, and elapsed limits.
- Revalidate current robots and public access behavior before catalogue requests. Record API or page mechanism, terms, copyright or rights, access-control, and retention decisions as current observations or pending blockers.
- Demonstrate deterministic resume for any multi-page run and idempotent completion without duplicate source, asset, request, blocker, or alias records.
- Record each discovered attachment URL only as a candidate with its current rights state. At most a metadata-safe `HEAD` may be used when explicitly allowed by robots and terms; never follow a `403` with another attempt.
- Produce separate completeness states and a combined source-universe gap report. Do not sum counts into a claim about the whole NJP Center universe unless duplicate and scope semantics are explicit.
- Keep live ledgers, request logs, manifests, cache, and provider facts outside Git. Commit only sanitized aggregate evidence and tests.
- Continue the second source when the first has a genuine source-specific blocker, provided the shared host policy remains allowed and total run bounds remain independent.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

After portable validation, run the newly documented source-specific commands on a trusted VM only.

## Allowed secrets

None. Authentication, cookies, signed links, and browser sessions are prohibited in this metadata proof.

## Artifact outputs

- Ignored source-specific ledgers, checkpoints, request facts, and manifests
- Sanitized aggregate NJP main-site and Video Archive completeness report
- Durable source and attachment blockers with next safe actions
- Exact-commit evidence manifest

## Stop conditions

- Stop the affected source on robots denial or ambiguity, `401`, `403`, login, repeated `429`, terms prohibition, unclear retention, changed shape, or any configured bound.
- Stop before downloading attachment or page content for retention.
- Stop if a public URL contains a signed or expiring access token, or if safe evidence would reveal provider or machine details.

## Human clarification protocol

Ask only if both source inventories are blocked and one reviewed endpoint choice is required to continue. Provide sanitized per-source outcomes, recommend the narrowest public metadata endpoint, and leave room for a different approved choice.

## Recommended response

Accept independent partial or blocked results and preserve every `403` attachment as a durable blocker. Continue with the separate Video Library and YouTube metadata branches rather than weakening NJP site controls.

## Trade-offs

Separate runs take more operator time but isolate source-specific failures and make completeness auditable. Refusing attachment retries may leave useful assets unavailable while respecting current access controls.

## Free-form response

Optional maintainer notes about the aggregate NJP site outcomes:
