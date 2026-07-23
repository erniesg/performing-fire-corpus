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

The live proof command is held and intentionally unavailable in this round.
Issue 8 must first record one approved stable asset
identifier and public URL, a strict byte bound, an expected media type, a
dedicated staging prefix, a proof window, and a reviewed retention rule or exact
key cleanup deadline. Only then may a trusted-VM operator construct a transfer
plan and invoke `transfer_approved_asset` with the reviewed HTTP and R2 client
for that one object. Until that approval, the only authorized command is:

```bash
performing-fire-corpus r2 readiness \
  --config .agent/storage.yaml \
  --output r2-readiness.json
```

Tests and CI use fake HTTP and storage clients and never perform a live upload.
