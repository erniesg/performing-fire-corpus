# Decide whether to approve one bounded staging-object proof

depends-on: 006,007
labels: rucksack-blocked

## Goal

Resolve the human rights and retention gate for at most one small public object in a dedicated R2 staging prefix. This issue authorizes no transfer until the reviewed response identifies an approved asset, a strict byte bound, and a reversible cleanup or retention decision.

## Acceptance tests

- The response references one stable asset identifier from the reviewed metadata ledger and its public source URL without copying source prose or content.
- The asset has an explicit `approved` rights record with a sanitized factual basis; `pending`, `blocked`, unclear, or platform-restricted assets remain ineligible.
- The response names a dedicated staging prefix, maximum byte size, expected media type, proof window, and either a deletion deadline or a reviewed retention rule.
- R2 readiness reports only required secret names and presence and is passing before the proof begins.
- The approved proof command is limited to one object, verifies SHA-256, size, media type, immutable key, and receipt, and does not overwrite a conflicting object.
- Cleanup, when selected, removes only the exact reviewed staging key and records a sanitized deletion receipt; no broad prefix deletion is permitted.
- If approval is declined or incomplete, the durable result remains blocked and identifies metadata-only work as the next safe action.
- No source content, signed URL, credential, account identifier, local path, or secret value is copied into this issue or its evidence.

## Validation command

```bash
python3 -m unittest discover -s tests -v
```

## Allowed secrets

- `CLOUDFLARE_ACCOUNT_ID`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_ENDPOINT`

Only the explicitly authorized trusted-VM proof may read their values. Discussion, dry runs, tests, issue updates, and evidence expose names and presence only.

## Artifact outputs

- Reviewed rights and retention decision in this issue
- At most one immutable staging object and one sanitized object receipt if approved
- Exact-key sanitized cleanup receipt if deletion is selected
- Durable blocked record if approval is declined or incomplete

## Stop conditions

- Stop if the asset, rights basis, byte bound, media type, dedicated prefix, or retention and cleanup decision is missing or ambiguous.
- Stop on robots, platform-policy, login, rate-limit, `403`, hash, size, media-type, or existing-object conflict.
- Stop if execution would expose or persist source content outside the approved object, or expose any secret value.

## Human clarification protocol

Reply with approval or decline. Approval must provide the stable asset identifier, public URL, sanitized rights basis, maximum bytes, expected media type, dedicated staging prefix, proof window, and exact retention or cleanup decision. Never paste a secret, signed URL, source content, account identifier, personal information, or local path. Free-form wording is allowed if all fields are unambiguous.

## Recommended response

Keep this issue blocked until the metadata-only proof is reviewed. Then approve one clearly downloadable, platform-permitted, small public object with the narrowest practical byte limit and an exact-key deletion deadline shortly after receipt verification.

## Trade-offs

Deleting quickly minimizes retained content but reduces later reproducibility; retaining the immutable object improves reproducibility but requires a stronger recorded rights basis and storage policy. Declining the proof preserves the metadata-only corpus without blocking additional discovery work.

## Free-form response

Decision: approve or decline

Stable asset identifier:

Public URL:

Sanitized rights basis:

Maximum bytes:

Expected media type:

Dedicated staging prefix:

Proof window:

Retention or exact-key cleanup decision:

Additional privacy-safe notes:
