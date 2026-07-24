# Run the bounded ANTIEGG source-universe inventory

depends-on: 016

## Goal

Run the expanded ANTIEGG adapter on a trusted VM to produce a current, resumable, metadata-only inventory and completeness report for the approved endpoints. This proof validates the adapter and source boundary; it does not authorize prose or media ingestion.

## Acceptance tests

- Run from a clean exact commit with explicitly reviewed request, page, response-byte, aggregate-byte, retry, rate, and elapsed-time limits recorded in the run plan.
- Revalidate current robots, endpoint availability, applicable terms record, and retention decision before each endpoint class; retain only sanitized observations and policy evidence.
- Demonstrate a durable page checkpoint and resume when more than one page is allowed, then demonstrate a terminal idempotent rerun without duplicate records or requests.
- Produce counts by endpoint, observed content type, unique stable ID, duplicate or alias, rejected unsafe field, blocker, and unvisited remainder. Label the outcome `bounded_partial`, `blocked`, or evidence-supported endpoint completeness.
- Keep live ledgers, request facts, cache, manifests, provider details, and command logs under ignored local state. Commit only an aggregate evidence update containing no response body, source prose, personal data, or machine-local path.
- Confirm no R2, media, prose, caption, transcript, embedding, browser session, or credential operation occurs.
- If any endpoint denies access or changes shape, preserve the blocker and continue only with independently allowed ANTIEGG endpoints within the original total budget.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

After portable validation, run the newly documented bounded ANTIEGG inventory command on a trusted VM only.

## Allowed secrets

None. Only public unauthenticated metadata endpoints are eligible.

## Artifact outputs

- Ignored live ANTIEGG ledger, checkpoints, sanitized request facts, and manifest
- Sanitized aggregate completeness report under `docs/`
- Durable endpoint blockers with exact safe next actions
- Evidence manifest tied to the exact commit

## Stop conditions

- Stop an endpoint on robots denial or ambiguity, `401`, `403`, repeated `429`, login, subscription, terms prohibition, unclear retention, shape drift, oversize, or budget exhaustion.
- Stop the run if redaction fails or response bodies, prose, media, personal details, credentials, provider data, or local paths would be retained.
- Stop before expanding limits or endpoints beyond the reviewed run plan.

## Human clarification protocol

Ask only if all approved metadata endpoints block and choosing a new endpoint is necessary for further ANTIEGG inventory. Present sanitized blockers, recommend preserving the current partial inventory, and require explicit later-source or endpoint approval.

## Recommended response

Accept a bounded partial result with explicit coverage gaps. Do not treat ANTIEGG inventory counts as complete or use this proof to authorize prose or media acquisition.

## Trade-offs

A larger but bounded metadata run improves discovery coverage while remaining secondary context. Strict endpoint-level stops may yield an uneven inventory, but prevent one denial from encouraging broader access workarounds.

## Free-form response

Optional maintainer notes about the aggregate outcome:
