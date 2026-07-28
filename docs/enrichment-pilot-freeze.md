# Metadata-only enrichment pilot freeze

`enrichment-pilot-freeze-20260727.json` freezes stable candidate identities for
issues #90, #91, #93, and #94 without authorizing or performing a
transformation. It contains no title text, source prose, captions, transcript
text, OCR output, local path, credential, or signed URL.

The candidate quotas are deliberately fixed:

- 30 videos: 15 NJP Video Library and 15 official NJP YouTube objects;
- 30 documents: 22 NJP Video Library PDFs and all eight archive-PDF controls;
- 30 page candidates: one deterministic in-range page identifier per frozen
  document;
- 30 NJP JPGs; and
- 30 cross-source linkage candidates: ten strong metadata matches, ten
  ambiguous metadata matches, and ten negative controls.

Selection uses stable identifier/hash ordering rather than source content.
Every output binds the exact input metadata-manifest digests. Re-running with
identical inputs produces identical output; changing any input snapshot changes
the freeze identifier. Publication requires an existing real parent directory,
uses an exclusive no-follow temporary file, and atomically links only to an
absent target. Existing or raced symlinks and different files are never followed
or overwritten. An identical existing regular file is accepted without
mutation.

## Fail-closed state

`candidate_set_state: frozen` means the identities cannot be silently replaced.
It does not mean processing is ready. The manifest remains
`execution_state: held` and `transformation_authorized: false`.

The current blockers are explicit:

- the selected NJP videos have exact object keys, sizes, and R2 presence
  receipts, but their available portable receipt exposes only multipart ETags,
  not SHA-256, and contains no duration;
- video MIME evidence and the speech-density, noise/music, language, era, and
  form strata needed for representative review are incomplete;
- JPG SHA-256 and sizes are present, but exact MIME and dimensions are not;
- page counts were inventoried for all 80 PDFs with `pdfinfo 26.03.0`, but the
  30 page identifiers still need visual stratification and a rendered-page
  digest before OCR;
- exact model/tool revisions and human quality thresholds remain pending; and
- issue #92 still holds every real ASR, native-text derivative, text-detection,
  and OCR operation.

Structural stop conditions are already fixed: do not start or immediately stop
on missing current authority, input-digest mismatch, output conflict,
provenance drift, cleanup failure, review-surface mismatch, or unbounded cache
or output. Quality thresholds remain pending because inventing them without
reviewed references would be misleading.

## Linkage boundary

The linkage slice compares only normalized public display-title metadata.
Similarity at or above 500/1000 is a strong candidate; 200–499/1000 is
ambiguous. The manifest stores only metadata/title fingerprints and stable
record IDs, never the title text itself.

No metadata score establishes byte identity. Every candidate remains
unreviewed, `merge_authorized: false`, and blocked from exact-content
deduplication unless matching content digests or another reviewed
byte-equivalence proof becomes available.

## Portable compiler

The compiler accepts explicit ignored metadata paths; it has no machine-local
defaults and never reads source media, captions, transcripts, page text, or
image pixels:

```bash
python -m performing_fire_corpus.pilot_freeze \
  --freeze-label njp_enrichment_pilot_20260727 \
  --njp-raw-manifest .local/njpvideo/raw-completeness.json \
  --njp-catalogue .local/njpvideo/catalogue.json \
  --youtube-media-manifest .local/youtube/media-completeness.json \
  --njp-pdf-manifest .local/njpvideo/pdf/manifest.json \
  --archive-pdf-manifest .local/njp-center/videoarchive/manifest.json \
  --image-manifest .local/njpvideo/image-acquisition.json \
  --center-catalogue .local/njp-center/catalogue.json \
  --archive-list .local/njp-center/videoarchive/archive-list.json \
  --pdf-page-counts .local/njpvideo/pdf-page-counts.json \
  --output local/enrichment-pilot-freeze.json
```

Omitting `--pdf-page-counts` is safe: the compiler emits exactly 30 unbound
page slots with `pending_page_inventory` rather than inventing page numbers.
The tool exits with a sanitized blocker if an input shape, candidate quota,
binding, or immutable output check fails.
