# NJP public raw-archive acquisition receipt — 2026-07-27

This is a content-free receipt for an explicitly requested operator run. It
records public source coverage and object-storage verification without
committing source titles, URLs, media, transcripts, document text, credentials,
signed values, account identifiers, or machine-local paths.

The run did **not** execute the repository's production ingestion or trusted
worker contracts. Those paths remain offline-only and held by their existing
gates. The assets were acquired with resumable out-of-band streaming tools and
must not be cited as proof that the product worker, ledger, rights checks,
retention logic, or content-addressed production namespace ran successfully.

## Declared public-online scope

The acquired content-bearing source set is complete for the finite catalogues
observed on 2026-07-27:

| Source set | Expected | Verified |
|---|---:|---:|
| NJP Video Library primary video assets | 401 | 401 |
| NJP Video Library primary image assets | 205 | 205 |
| NJP Video Library primary PDF assets | 72 | 72 |
| NJP Video Library primary records | 678 | 678 |
| Human SRT attachments | 38 | 38 |
| Linked analogue-video-archive PDFs | 8 | 8 |
| Official NJP YouTube playable audio-video assets | 156 | 156 |

One public playable representation was retained per video record. NJP Video
Library records use the low proxy when available and the canonical proxy as the
fallback. Official YouTube uploads use combined MP4 format 18 at 360p. These
are processing inputs and public representations, not preservation masters.

Player sprite VTT, thumbnails, sprite sheets, and duplicate bitrate
representations were excluded because they do not add source content. The
stated 2,285 physical analogue tapes and original preservation masters are not
publicly exposed files and are therefore outside the completed online scope.

## Execution and verification

- NJP Video Library media streamed from the trusted VM directly to object
  storage. No media was staged on VM disk.
- Official YouTube media streamed from the trusted laptop directly to object
  storage because the VM's datacenter route was blocked by YouTube.
- Every new stream was completed as an exact multipart object, checked by exact
  key and byte size, and recorded in a local acquisition manifest.
- The final NJP verification swept 732 exact object keys.
- The final YouTube verification swept all 156 exact media keys.
- No incomplete NJP or YouTube multipart upload remained.
- The final object-storage inventory was 938 objects and 79,793,160,059 bytes
  across the wider bucket, including pre-existing ANTIEGG and transcript
  objects.

The durable object-storage receipts are:

```text
njpvideo/manifests/raw-completeness-20260727.json
youtube/manifests/media-completeness-20260727.json
manifests/njp-public-raw-archive-completeness-20260727.json
```

The content-free cross-source receipt is also checked in as
`docs/njp-public-raw-archive-completeness-20260727.json`.

## Remaining processing

This receipt authorizes and claims no derived transformation. The remaining
work is:

- ASR with model-specific provenance;
- native text extraction and selective page-level OCR;
- human review of real NJP samples and exact-output review receipts;
- source cross-linking, deduplication, and semantic indexing.
