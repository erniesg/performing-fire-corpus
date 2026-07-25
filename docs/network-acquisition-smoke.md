# Opt-in network acquisition smoke run

This opt-in metadata-only check runs manually on a trusted VM. It is unauthenticated,
uses two public `GET` requests at most, consults robots metadata first, and
writes only a SQLite ledger plus a sanitized manifest. It must not be added to portable CI.

The live-state root `.local/network-smoke/` is ignored by an exact `.gitignore`
rule, so its ledger and manifest never become repository content. Create that
directory, then run:

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

## ANTIEGG metadata endpoints are not in this run

This smoke run covers the `antiegg-article` endpoint only. The broader public
sitemap and WordPress metadata endpoints have their own held adapters,
described in `docs/antiegg-metadata-adapters.md`. Those adapters raise
`SourceShapeUnreviewed` and emit no request, so there is no metadata-only
command to run for them yet.

Before either endpoint can join a bounded run, it needs its own current
robots, API, terms, copyright, access, and retention decisions. A missing or
stale decision blocks that endpoint alone and never blocks another source.
ANTIEGG prose and media stay `blocked` or `pending` regardless: this site is
secondary editorial context, and public readability is not ingestion
permission.
