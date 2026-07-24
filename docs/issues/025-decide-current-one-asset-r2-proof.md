# Decide the current one-asset R2 proof

depends-on: 011,024
labels: rucksack-blocked

## Goal

Provide or decline a fresh, exact approval for at most one small public asset to exercise the already implemented trusted-VM upload, exact-key `HEAD`, and exact-key deletion or reviewed retention path. This proof validates the pipeline only; it does not define the corpus boundary or authorize broader acquisition.

## Acceptance tests

- Select exactly one stable qualified asset from current metadata evidence; do not reuse an earlier approval, proof window, signed URL, cookie, token, response, or unverified inventory assumption.
- Record a current robots allowance for the exact host and URL, current platform and rights permission for download and proof storage, expected final public URL, exact MIME type, positive byte limit, and short UTC proof window.
- Confirm a dedicated proof bucket and prefix scope, all required R2 secret names as `present` or `missing`, and a reviewed cleanup or retention decision without exposing values, account identifiers, endpoints, or provider details.
- Prefer `delete_after_verification` with an exact UTC deadline. A retention decision must instead state its reviewed purpose, expiry, deletion owner, and why retained source content is authorized.
- Require upload to one immutable content-addressed key, exact-key `HEAD` verification, and either deletion of only that verified key plus an absence check or reviewed retention. No listing or broad deletion is authorized.
- Keep approval, live ledgers, receipts, cache, and provider details in ignored local state; the issue contains only privacy-safe fields and outcome status.
- If no eligible current asset exists, record `decline` and continue independent NJP, YouTube, ANTIEGG, selection, search, and contract work.

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

Only the later approved trusted-VM run may read values. This issue records names and `present` or `missing` only.

## Artifact outputs

- Reviewed approve or decline decision in this issue
- One strict current approval document under ignored `.local/r2-proof/` only when approved
- Durable blocker when any approval field is incomplete
- No object or network side effect from resolving this issue alone

## Stop conditions

- Stop if the candidate lacks current robots allowance, explicit rights, platform permission, final URL, MIME, byte bound, proof window, dedicated scope, secret-name presence, and cleanup or retention decision.
- Stop if a prior credential, cookie, token, signed URL, account identifier, endpoint value, private material, or source content would be reused or reproduced.
- Stop if the approval selects multiple assets, a prefix wildcard, broad storage scope, or an open-ended proof window.

## Human clarification protocol

Reply with approve or decline using privacy-safe fields only. Never paste secret values, account identifiers, endpoint values, signed URLs, cookies, source content, personal details, screenshots, or local paths. Leave unknown fields blank and keep the issue blocked.

## Recommended response

```text
Decision: approve or decline
Stable asset identifier:
Stable source identifier:
Exact public HTTPS URL:
Current robots evidence reference:
Sanitized rights and platform basis:
Expected MIME type:
Maximum bytes:
Proof window in UTC:
Dedicated proof bucket name:
Dedicated staging prefix:
Cleanup decision: delete_after_verification or reviewed retention
Exact-key cleanup deadline or retention expiry in UTC:
R2 secret-name presence: all present, incomplete, or not checked
Storage scope reviewed for this bucket and prefix only: yes or no
Additional privacy-safe notes:
```

If no currently qualified asset is available, decline now and revisit after metadata and rights qualification produces one.

## Trade-offs

Immediate deletion minimizes retained content and proves reversible mechanics but not long-term corpus durability. Waiting for a defensible asset delays the live proof while preventing pipeline validation from becoming accidental acquisition authority.

## Free-form response

Optional privacy-safe maintainer response using different wording:
