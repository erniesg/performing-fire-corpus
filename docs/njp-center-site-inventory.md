# Bounded NJP Center site inventory proof

This trusted-VM command runs the `njp-center-main` and
`njp-center-video-archive` proofs as independent resumable runs. Each source
gets its own run plan, SQLite ledger, policy snapshot, checkpoint, sanitized
request facts, blockers, and completeness report under the ignored
`.local/njp-center-inventory/` root.

The `njp-center-main` proof now inventories the shape-bound
`/mediaObjects/more?page=<n>` fragments after an allowed `robots.txt` result.
It stops on the first valid zero-item page and retains only public identifier,
canonical detail URL, and title. The separate Video Archive source remains
held and performs only its metadata-safe `HEAD`. Redirects are not followed.
No response body, attachment, image, audio, video, caption, transcript,
cookie, credential, or browser state is retained.

Run the current proof only on a trusted VM:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus inventory-njp-sites \
  --run-label issue84-20260726-final \
  --state-root .local/njp-center-inventory/issue84-20260726-final \
  --aggregate-report docs/njp-center-site-inventory-report.json \
  --governance config/source-governance.v1.json \
  --max-requests 6 \
  --max-pages 5 \
  --max-response-bytes 65536 \
  --aggregate-bytes 131072 \
  --retries 1 \
  --max-retry-after 2 \
  --rate-limit 1 \
  --timeout 10 \
  --max-elapsed 30
```

The limits apply independently to each source. A genuine source-specific
robots, access, rate, or shape blocker stops only that source; the other source
continues within its own limits. A `401` or `403` is terminal for that exact
request and is never followed by credential use, referer manipulation, an
alternate route, or another attachment attempt.

Repeating the exact command against a terminal run reads the two stored reports
without making requests and writes a byte-identical aggregate report. For a
future reviewed multi-page catalogue run, the existing bounded-discovery
checkpoint engine remains mandatory; it rejects pagination loops and duplicate
source, record, request, blocker, and alias identities.

The aggregate report deliberately does not add source counts. Cross-source
duplicate semantics remain unknown and aliases are source-local, so these two
endpoint proofs cannot support a claim about the whole NJP Center universe.
