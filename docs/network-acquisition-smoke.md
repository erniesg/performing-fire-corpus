# Opt-in network acquisition smoke run

This opt-in metadata-only check runs manually on a trusted VM. It is
unauthenticated, consults robots metadata first, stays inside the request,
elapsed, and byte bounds passed on the command line, and writes only a SQLite
ledger plus a sanitized manifest. It must not be added to portable CI.

## The article endpoint

The `antiegg-article` run uses two public `GET` requests at most. Its live-state
root `.local/network-smoke/` is ignored by an exact `.gitignore` rule, so its
ledger and manifest never become repository content. Create that directory,
then run:

```bash
mkdir -p .local/network-smoke
PYTHONPATH=src python3 -m performing_fire_corpus inventory-public \
  --source antiegg-fluxus \
  --max-requests 2 \
  --timeout 10 \
  --rate-limit 2 \
  --retries 1 \
  --max-elapsed 30 \
  --max-response-bytes 1048576 \
  --ledger .local/network-smoke/ledger.sqlite3 \
  --sanitized-manifest .local/network-smoke/manifest.json
```

The command stops and records a durable blocker on robots denial, access
restriction, exhausted rate limiting, an unapproved redirect, an unexpected
metadata shape or MIME type, or an oversized response. Do not add credentials,
increase bounds to bypass a restriction, or retain response bodies.

The `--max-response-bytes` default is `1048576`. The earlier `262144` was
fitted to a fixture rather than to the live page: the live article is 262145
bytes, so every run stopped on byte count before the adapter could report what
had actually changed about the page.

## Resuming a ledger that already holds a result

This lane is resumable, and a resumed run replays the stored terminal result
without making a request. The manifest then carries `stored_result_replay`,
naming the bounds the stored result was recorded under alongside the bounds of
the current invocation. Raising a bound and seeing the same blocker with
`"bounds_changed": true` means the stored answer is being replayed, not
re-tested; use a fresh ledger to re-attempt under the new bounds.

## The posts endpoint is a separate bounded run

`--source antiegg-posts` inventories the `antiegg-posts-api` endpoint, which is
the one ANTIEGG surface that yields the whole catalogue. It is the shape-bound
adapter described in `docs/antiegg-metadata-adapters.md`. It paginates at
`per_page=100` and needs its own page bound. Its live state root
`.local/antiegg-inventory/` is ignored by an exact `.gitignore` rule:

```bash
mkdir -p .local/antiegg-inventory
PYTHONPATH=src python3 -m performing_fire_corpus inventory-public \
  --source antiegg-posts \
  --max-requests 24 \
  --max-pages 20 \
  --timeout 25 \
  --rate-limit 2 \
  --retries 2 \
  --max-elapsed 300 \
  --max-response-bytes 1048576 \
  --ledger .local/antiegg-inventory/ledger.sqlite3 \
  --sanitized-manifest .local/antiegg-inventory/manifest.json
```

The manifest reports `complete` only when the unique record ids retrieved equal
the `x-wp-total` the endpoint declared; reaching the last page while short of
that total is reported as a `declared_total_mismatch` blocker. Exhausting a
configured budget is reported as `bounded_stop`, not as a source blocker.
Unlike the article lane, this one re-attempts on every invocation and never
replays a stored verdict, so each run's answer is its own.

The sitemap adapter still raises `SourceShapeUnreviewed` before emitting a
request, so no CLI path drives it.

Before a live endpoint joins a bounded run, its current robots, API, terms,
copyright, access, and retention decisions must pass. A missing or stale
decision blocks that endpoint alone and never blocks another source.
ANTIEGG prose and media stay `blocked` or `pending` regardless: this site is
secondary editorial context, and public readability is not ingestion
permission.
