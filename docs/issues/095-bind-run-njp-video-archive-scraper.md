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

## Scope

Retain only stable public identifiers, canonical detail URLs, language/date/
type/classification facts, and short content-neutral display titles required
to identify records. Do not request or retain descriptions, attachments,
images, audio, video, captions, transcripts, OCR, ASR, or transformed bytes.
Keep counts separate from every other NJP source.

## Validation

```bash
sh scripts/preflight-python -m unittest discover -s tests
scripts/agent-evidence
```
