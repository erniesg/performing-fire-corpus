# Opt-in network acquisition smoke run

This opt-in metadata-only check runs manually on a trusted VM. It is unauthenticated,
uses two public `GET` requests at most, consults robots metadata first, and
writes only a SQLite ledger plus a sanitized manifest. It must not be added to portable CI.

Create an ignored local output directory, then run:

```bash
mkdir -p .local/network-smoke
PYTHONPATH=src python3 -m performing_fire_corpus inventory-public \
  --source antiegg-fluxus \
  --max-requests 2 \
  --timeout 10 \
  --rate-limit 2 \
  --retries 1 \
  --max-elapsed 30 \
  --max-response-bytes 262144 \
  --ledger .local/network-smoke/ledger.sqlite3 \
  --sanitized-manifest .local/network-smoke/manifest.json
```

The command stops and records a durable blocker on robots denial, access
restriction, exhausted rate limiting, an unapproved redirect, an unexpected
metadata shape or MIME type, or an oversized response. Do not add credentials,
increase bounds to bypass a restriction, or retain response bodies.
