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
