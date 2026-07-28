# Bind and run the separate NJP Center Video Archive scraper

## Goal

Review the current public shape of `https://njp.ggcf.kr/pages/videoarchive`,
bind the source-distinct adapter to that exact shape, and run a bounded factual
metadata inventory.

## Execution route

Pin the repository commit, adapter and schema versions, request/page/byte/rate/
retry/elapsed limits, and stop thresholds before a live catalogue request.
Run the exact commit on the trusted VM first. A trusted-laptop retry is allowed
only when the VM receipt classifies a host or network capability mismatch. The
same commit and bounds must be used, and its sanitized receipt resumes the
VM-coordinated task.

Robots, terms, rights, authentication, access-control, rate-limit, retention,
and unreviewed-shape failures are hard stops. They never authorize a laptop
fallback.

## Stage-one shape review

The portable, zero-network implementation is validated before this command is
run on the trusted VM:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus \
  review-njp-video-archive-shape \
  --commit-sha <full-exact-head-sha> \
  --governance config/source-governance.v1.json \
  --output .local/njp-video-archive-shape/issue95-vm.json \
  --max-response-bytes 131072 \
  --rate-limit 1 \
  --timeout 10 \
  --max-elapsed 30
```

The probe requests `robots.txt`, then at most one public archive page. It
follows no redirect and retains no raw HTML or prose. Its output contains only
bounded request facts, response hashes, allowlisted tag categories, attribute
name categories, categorical URL shapes, and embedded-JSON type/depth/count
shapes with key-set digests. Ordinary HTML optional-end-tag recovery is
reported as a count and is distinct from a capacity truncation. The report
stores no raw source-derived class, ID, data-attribute, URL, JSON-key,
transport-error, or MIME strings.

The exact VM review at commit
`4367446b1b092020ccb95181ade3a2c93a44b944` completed with no blocker.
It observed a 53,358-byte HTML page, 98 categorical signature shapes, no
embedded JSON, one ordinary HTML recovery event, no capacity truncation, and
structure digest
`e6f9a2911a325fb321202b5994b257ec50ae48bf91a60553f64e38cc33e8851b`.
See `docs/njp-center-video-archive-shape.md`.

## Stage-two bounded inventory

The v2 adapter binds that receipt to exactly eight unique same-host public PDF
catalogue links and their bounded anchor labels. Run it independently on the
trusted VM:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus inventory-njp-sites \
  --source njp-center-video-archive \
  --run-label issue95-video-archive \
  --commit-sha <full-exact-head-sha> \
  --state-root .local/njp-center-inventory/issue95-video-archive \
  --aggregate-report docs/njp-center-video-archive-inventory-report.json \
  --governance config/source-governance.v1.json \
  --max-requests 2 \
  --max-pages 1 \
  --max-response-bytes 65536 \
  --aggregate-bytes 65536 \
  --retries 0 \
  --max-retry-after 2 \
  --rate-limit 1 \
  --timeout 10 \
  --max-elapsed 30
```

The exact VM run at commit
`bda79f0ef7d098c9acea5d9845e031ed39d98e40` completed on 2026-07-27.
It made two requests, committed one page, retained eight unique metadata
records, matched the reviewed structure digest exactly, and reported no
blocker. It requested no linked-object bytes and did not use the laptop
fallback. See `docs/njp-center-video-archive-inventory-report.json`.

## VM/laptop attempt receipts

`performing_fire_corpus.njp_video_archive_route` and the offline
`record-njp-video-archive-attempt` command bind a sanitized source report to
the exact run-plan hash, commit, adapter, reviewed shape, counts, lane, and
optional parent receipt. The receipt omits retained records, URLs, titles,
response bodies, errors, local paths, and credentials.

A generic `transport_error` remains held. Laptop fallback becomes eligible
only when the VM attempt has no other blocker and also binds the SHA-256 of a
separate sanitized runner-capability diagnostic to one of four closed codes:
VM DNS, IP-route, outbound-HTTPS, or TLS capability unavailable. Terms,
rights, robots, authentication, access-control, rate-limit, retention, source
shape, MIME, size, request, page, and elapsed blockers cannot carry a
capability-mismatch code and therefore cannot route to the laptop.

The laptop receipt must name the VM receipt and reproduce its exact commit,
run-plan hash, and reviewed-shape digest. A successful laptop result routes
back to `trusted-vm` as `resume_vm_from_laptop_receipt`; a laptop blocker is
terminal and cannot recurse to another fallback. Record one attempt and its
next route without making a network request:

```bash
PYTHONPATH=src python3 -m performing_fire_corpus \
  record-njp-video-archive-attempt \
  --lane trusted-vm \
  --report .local/njp-center-inventory/<run>/njp-center-video-archive/completeness-report.json \
  --plan .local/njp-center-inventory/<run>/njp-center-video-archive/run-plan.json \
  --attempt-output .local/njp-center-inventory/<run>/vm-attempt.json \
  --route-output .local/njp-center-inventory/<run>/route.json
```

The completed 2026-07-27 VM proof routes directly to `complete_on_vm`, so no
laptop execution is authorized or necessary for that run.

## Scope

Retain only stable source identities, canonical public PDF-link URLs, and
bounded anchor labels required to identify the eight records. Do not request
or retain descriptions, linked PDFs, images, audio, video, captions,
transcripts, OCR, ASR, or transformed bytes. Keep counts separate from every
other NJP source.

Before any request, stage two requires the exact clean named commit and current
endpoint authority for `metadata_inventory`, `public_retrieval`, and
`retention` through the full run horizon. Its plan and receipts bind both the
commit and stage-one structure digest. It recomputes that categorical digest
from the live body and requires an exact match before retaining any of the
eight records. The Video Archive source makes at most two requests:
`robots.txt`, then the one reviewed archive page.

## Validation

```bash
sh scripts/preflight-python -m unittest discover -s tests
scripts/agent-evidence
```
