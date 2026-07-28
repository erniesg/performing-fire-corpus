# Bounded NJP Center site inventory proof

This trusted-VM command runs the `njp-center-main` and
`njp-center-video-archive` proofs as independent resumable runs. Each source
gets its own run plan, SQLite ledger, policy snapshot, checkpoint, sanitized
request facts, blockers, and completeness report under the ignored
`.local/njp-center-inventory/` root.

The `njp-center-main` proof inventories the shape-bound
`/mediaObjects/more?page=<n>` fragments after an allowed `robots.txt` result.
It stops on the first valid zero-item page and retains only public identifier,
canonical detail URL, and title. The separate Video Archive source performs one
shape-bound `GET`, retains exactly eight public PDF-link URLs and bounded link
titles, and terminates without requesting any linked object. Redirects are not
followed. No source body, attachment, image, audio, video, caption, transcript,
cookie, credential, or browser state is retained after parsing.

Run the current proof only on a trusted VM:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus inventory-njp-sites \
  --run-label issue84-20260726-final \
  --commit-sha <full-exact-head-sha> \
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

The command first requires the named exact clean Git head, validates the
complete governance registry, and requires `metadata_inventory`,
`public_retrieval`, and `retention` authority to remain eligible through each
source's full elapsed-time horizon. A governance failure makes zero requests.
The reviewed shape digest and exact commit are bound into the run plan,
per-source receipt, and aggregate receipt. For the Video Archive, stage two
recomputes the same content-neutral categorical structure digest from the live
page and requires an exact match before its metadata parser may retain any
record.

The request, page, byte, retry, and elapsed limits apply independently to each
source. The same-host rate limiter is shared across selected sources, including
the transition from the first source's last request to the second source's
`robots.txt` request. A genuine source-specific
robots, access, rate, or shape blocker stops only that source; the other source
continues within its own limits. A `401` or `403` is terminal for that exact
request and is never followed by credential use, referer manipulation, an
alternate route, or another attachment attempt.

Limits also have fixed implementation ceilings: at most 16 requests, 8 pages,
2 retries, 128 KiB per response, 512 KiB aggregate response bytes, 10 seconds
for retry-after and same-host intervals, 30 seconds per request timeout, and
120 seconds elapsed. The transport receives the remaining aggregate byte
budget for every attempt, so it cannot read a full per-response allowance
after the aggregate remainder becomes smaller.

Repeating the exact command against a terminal run reads the two stored reports
without making requests and writes a byte-identical aggregate report. For a
future reviewed multi-page catalogue run, the existing bounded-discovery
checkpoint engine remains mandatory; it rejects pagination loops and duplicate
source, record, request, blocker, and alias identities.

To run only the separate Video Archive source, add:

```bash
--source njp-center-video-archive \
--max-requests 2 \
--max-pages 1 \
--max-response-bytes 65536 \
--aggregate-bytes 65536 \
--retries 0
```

The aggregate report deliberately does not add source counts. Cross-source
duplicate semantics remain unknown and aliases are source-local, so these two
endpoint proofs cannot support a claim about the whole NJP Center universe.

The separate Video Archive route contract records a content-free attempt
receipt after this command. It holds ordinary transport failures and permits
the exact plan to move from VM to laptop only when a separate sanitized
runner-capability diagnostic proves a closed host/network capability mismatch.
See `docs/issues/095-bind-run-njp-video-archive-scraper.md`. The successful
2026-07-27 VM run did not use or require that fallback.
