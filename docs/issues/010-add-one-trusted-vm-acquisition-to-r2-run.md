# Add one trusted-VM acquisition-to-R2 operator run

depends-on: 009
labels: rucksack-blocked

## Goal

Add a trusted-VM operator command that performs at most one bounded, robots-allowed, rights-approved public acquisition into the dedicated R2 staging scope, verifies the immutable exact key, applies the reviewed exact-key cleanup decision, and emits only sanitized receipts and evidence. Keep the live proof blocked until the human gate below is complete.

## Acceptance tests

- Add `performing-fire-corpus trusted-vm acquire-one-to-r2` with explicit repository-relative paths for a reviewed approval document, durable ledger, `.agent/storage.yaml`, disposable cache, and sanitized output directory. Reject defaults that could select multiple assets, a broad storage scope, or an unbounded destination.
- Validate the approval document before any network action: exactly one stable asset and source identifier, public HTTPS URL, complete matching `approved` rights record and sanitized basis, expected MIME type, positive maximum bytes, proof window, dedicated staging prefix matching configuration, and `delete_after_verification` exact-key cleanup decision with deadline.
- Run redacted R2 readiness before public acquisition and fail closed when any credential name is missing, the endpoint is invalid, the bucket or dedicated prefix is absent, or the prefix-bounded storage probe is not approved. Output only check names and `present` or `missing`.
- Recheck applicable robots rules with a bounded unauthenticated request immediately before acquiring the asset. Allow only the reviewed public host and exact final URL; fail closed on robots denial or ambiguity, login or subscription requirements, `401`, `403`, disallowed redirect, exhausted rate limit or retry budget, unexpected status, or stale proof window.
- Make at most the documented robots request plus one bounded asset request. Stream through disposable cache into the existing immutable transfer contract, enforce declared and observed size bounds and the single expected MIME type, and remove all cache files after success or failure.
- Verify the returned exact key with one `HEAD` and match key, asset identifier, byte size, MIME type, and SHA-256 to the durable object receipt. Never retrieve the stored object, list the bucket, list a prefix, or emit source bytes.
- Delete only the exact verified key after successful verification, then verify that the same exact key is absent. Never accept a prefix, wildcard, bucket, or caller-supplied deletion key. Treat already-absent cleanup as idempotent only when the durable verified receipt identifies that exact key.
- Atomically emit a versioned sanitized run manifest plus readiness, request-fact, object, exact-key verification, and exact-key cleanup receipts. Artifacts may contain stable record identifiers, public URL, timestamps, status class, MIME type, byte size, SHA-256, immutable object key, outcome code, and next safe action; they must not contain source bodies, raw HTML, media, headers, cookies, signed URLs, credentials, account identifiers, secret values, private material, or local paths.
- Persist durable blocked results and a safe next action when rights, robots, MIME, size, credentials, proof window, cleanup decision, or storage scope is not approved. A failed run must never silently weaken a bound, leave an unrecorded object, or continue to another asset.
- Use red/green/refactor TDD with fake public HTTP and R2 clients. Cover every preflight failure before network, robots denial, MIME and size mismatch, credential and scope failure, exact-key verification conflict, interrupted transfer, cleanup failure, idempotent resume, output redaction, and a successful one-object upload/verify/delete flow. Automated tests and portable CI make zero live network requests.
- Update the trusted-VM operator documentation with the held human gate and post-unlock command below. Do not add generated proof artifacts to Git, and keep model or effort racing out of scope.

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

Only the explicitly approved post-unlock trusted-VM run may read their values from the VM secret store. The command, readiness result, receipts, evidence, logs, issue discussion, tests, and Git expose names and `present` or `missing` only.

## Artifact outputs

- Trusted-VM one-object orchestration and exact-key cleanup support under `src/performing_fire_corpus/`
- Fake-client operator and CLI tests under `tests/`
- Updated trusted-VM R2 operator documentation under `docs/`
- Sanitized live receipts under `.local/r2-proof/` only after human unlock; this directory and its contents remain untracked
- One durable blocked result when the human response or any fail-closed gate is incomplete

## Stop conditions

- Stop before all network access if the human gate, rights approval, public URL, proof window, MIME, byte bound, cleanup deadline, credential presence, bucket, dedicated prefix, or prefix-bounded storage scope is absent, inconsistent, expired, or ambiguous.
- Stop on robots denial or ambiguity, access control, login, subscription requirement, `401`, `403`, rate or retry exhaustion, final-URL mismatch, unexpected MIME, declared or observed oversize, hash or receipt conflict, or exact-key verification failure.
- Stop cleanup unless the command itself just verified the exact immutable key from the durable receipt. Never delete by bucket, prefix, wildcard, user-pasted key, or broad selection.
- Stop and sanitize if any output could contain personal information, private documents, PDFs, source bodies, raw HTML, media, credentials, account identifiers, cookies, signed URLs, secret values, provider error bodies, or machine-local paths.
- Stop if generated evidence is staged for Git or if execution would introduce model racing, full-corpus acquisition, browser authentication, or access-control bypass.

## Human clarification protocol

Keep this issue labeled `rucksack-blocked` until a maintainer provides every privacy-safe approval field below and confirms trusted-VM secret presence without pasting values.

Use the current Account API token flow below. The direct dashboard link is the
primary action; the documentation links are supporting context:

- Create the scoped Account API token:
  `https://dash.cloudflare.com/?to=%2F%3Aaccount%2Fapi-tokens%2Fcreate`
- Inspect R2 buckets:
  `https://dash.cloudflare.com/?to=%2F%3Aaccount%2Fr2%2Foverview`
- R2 S3 setup:
  `https://developers.cloudflare.com/r2/get-started/s3/`
- R2 authentication:
  `https://developers.cloudflare.com/r2/api/tokens/`
- Exact-key deletion behavior: `https://developers.cloudflare.com/r2/objects/delete-objects/`

In the current Cloudflare dashboard:

1. Open the token link above. If navigating manually, use `Manage account` >
   `Account API tokens` > `Create Token`.
2. Give the credential a recognizable name.
3. In `Permission policies`, change the resource selector to `R2 Buckets` and
   select `performing-fire-corpus-proof`. The R2 Overview table is paginated;
   the token picker shows the full bucket list.
4. Under `Developer Platform`, enable
   `Workers R2 Storage Bucket Item Read` and
   `Workers R2 Storage Bucket Item Write` using the `Read` and `Edit`
   checkboxes.
5. Create the token and keep the one-time confirmation page open. Copy the
   values labelled `Access Key ID`, `Secret Access Key`, and `S3 API endpoint`
   into the guided Rucksack form. Rucksack derives the account identifier from
   the endpoint, so the human should not hunt for or paste a separate account
   ID.

Do not use the generic token value in place of the S3 credentials, grant
account-wide administration, or make the bucket public.

Before the human leaves the token form, the expected scoped policy looks like:

```text
Permission policies
  Resource: R2 Buckets
  Bucket: performing-fire-corpus-proof
  Developer Platform:
    Workers R2 Storage Bucket Item Read   [Read]
    Workers R2 Storage Bucket Item Write  [Edit]
```

The one-time confirmation screen then looks like this redacted sample:

```text
Access Key ID: present
Secret Access Key: present
S3 API endpoint: https://••••••••.r2.cloudflarestorage.com
```

Do not attach an unredacted screenshot. The reviewed response may name the
dedicated bucket and prefix but must never include an account identifier,
credential, endpoint value, source content, signed URL, personal information,
private document, or local path.

After the response is complete, implementation is merged, and all four secret names report `present` on the trusted VM, the post-unlock proof command is exactly:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus trusted-vm acquire-one-to-r2 \
  --approval .local/r2-proof/approval.json \
  --database .local/r2-proof/ledger.sqlite3 \
  --storage-config .agent/storage.yaml \
  --cache-directory .local/r2-proof/cache \
  --sanitized-output .local/r2-proof/receipts
```

Run `infra/vm/verify.sh` immediately before and after that command. Do not run the proof from portable CI, a hosted runner, or an untrusted machine.

## Recommended response

Approve only one clearly downloadable, platform-permitted, small public asset and use the narrowest practical bound:

```text
Decision: approve or decline
Stable asset identifier:
Stable source identifier:
Public HTTPS URL:
Sanitized rights basis:
Expected MIME type:
Maximum bytes:
Proof window in UTC:
Dedicated proof bucket name:
Dedicated staging prefix:
Cleanup decision: delete_after_verification
Exact-key cleanup deadline in UTC:
Trusted-VM secret presence:
  CLOUDFLARE_ACCOUNT_ID: present or missing
  R2_ACCESS_KEY_ID: present or missing
  R2_SECRET_ACCESS_KEY: present or missing
  R2_ENDPOINT: present or missing
Storage scope reviewed for this bucket only: yes or no
Additional privacy-safe notes:
```

If any field is unknown, decline or leave it blank and keep the issue blocked. Never paste secret values or an account identifier.

## Trade-offs

Immediate exact-key deletion minimizes retained corpus content and makes the first production exercise reversible, but it proves handoff mechanics rather than long-term retention. Requiring a fresh robots check and a dedicated bucket scope adds setup and request overhead while making the rights and storage boundaries independently reviewable.

## Free-form response

Optional maintainer response using different privacy-safe wording, provided every required field is unambiguous:
