# R2 object-storage boundary

The metadata-only readiness check is safe to run without a live transfer:

```bash
performing-fire-corpus r2 readiness \
  --config .agent/storage.yaml \
  --output r2-readiness.json
```

It atomically persists and prints configuration field names, required secret
names, the staging-scope probe name, and `present` or `missing` only. It never
prints configuration values, account identifiers, secret values, or storage
client errors. Without a configured storage client, the scope probe fails
closed.

The low-level transfer command is available for one reviewed approval plan:

```bash
performing-fire-corpus r2 transfer-approved \
  --plan APPROVAL_JSON \
  --ledger LEDGER_SQLITE3 \
  --config .agent/storage.yaml \
  --cache-directory DISPOSABLE_CACHE \
  --output SANITIZED_RECEIPT_JSON
```

Adding this wiring does not authorize a live transfer. The plan must contain one
complete approved rights record, public URL, media allowlist, byte bound,
dedicated prefix matching the storage configuration, and reviewed retention or
cleanup decision. Missing or malformed gates stop before either client is
constructed. The command writes the verified receipt but prints only a stable
status or sanitized error code and next action.

Tests and CI use fake HTTP and storage clients and never perform a live upload.

## Held trusted-VM one-object proof

The end-to-end operator command remains held until a maintainer supplies one
complete reviewed approval and confirms all four R2 secret names are `present`
on the trusted VM. It is not a batch command and accepts no default or broad
destination:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus trusted-vm acquire-one-to-r2 \
  --approval .local/r2-proof/approval.json \
  --database .local/r2-proof/ledger.sqlite3 \
  --storage-config .agent/storage.yaml \
  --cache-directory .local/r2-proof/cache \
  --sanitized-output .local/r2-proof/receipts
```

The approval is a strict version-1
`trusted_vm_acquisition_approval` JSON object. It names exactly one
`asset_id`, one `source_id`, one public HTTPS URL, one complete matching
approved rights record with a sanitized basis, one `expected_mime_type`, a
positive `maximum_bytes`, a current UTC `proof_window`, the reviewed
`staging_bucket` and `staging_prefix`, an `evidence_ref`, and:

```json
{
  "cleanup_decision": "delete_after_verification",
  "cleanup_deadline": "YYYY-MM-DDTHH:MM:SSZ"
}
```

Before the public request, the command validates the approval, endpoint shape,
bucket and prefix match, required secret-name presence, and the bounded storage
scope probe. It then makes one bounded unauthenticated robots request and, only
when that exact host and URL are allowed, one bounded asset request. The asset
is streamed through disposable cache, uploaded immutably, checked by exact key,
deleted only by that verified key, and checked absent. Redirects, access
control, rate exhaustion, stale windows, ambiguous robots rules, MIME or size
mismatch, conflicting metadata, and cleanup failure all stop closed.

The receipt directory contains only atomic sanitized JSON facts: readiness,
the robots request fact, the object receipt, exact-key verification, exact-key
cleanup, and the versioned run manifest. If an R2 create response is lost, an
`upload-attempt` fact records the exact content-addressed key and whether the
follow-up exact-key `HEAD` found it absent, conflicting, or unverifiable. It is
not an object receipt and never authorizes deletion. A blocked run records a
stable outcome code and next safe action. Source bytes, bodies, headers,
cookies, signed URLs, credentials, account identifiers, provider errors, and
local paths are never receipt fields. Every cache file created by the run is
removed after success or failure, and a completed run resumes without another
public or storage request. A cleanup retry is allowed only when the durable
object and verification receipts identify the same exact key.

Run `infra/vm/verify.sh` immediately before and after the held command. Do not
run it from portable CI or a hosted runner. Do not commit `.local/r2-proof/`,
its ledger, cache, receipts, approval, or generated evidence.
