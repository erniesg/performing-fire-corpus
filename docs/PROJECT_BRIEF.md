# Public project brief

## Purpose

Build a searchable research corpus that can later support AI-assisted
performance-score generation for an interactive artwork about people and
technology. This repository contains the acquisition software, public source
inventory, rights decisions, and sanitized evidence—not the research corpus
itself.

## Public source universe

- [Nam June Paik Art Center Video Library](https://njpvideo.ggcf.kr/)
- [Nam June Paik Art Center](https://njp.ggcf.kr/)
- [Video Archive](https://njp.ggcf.kr/pages/videoarchive)
- [Official YouTube channel](https://www.youtube.com/@NamJunePaikArtCenter/videos)
- [ANTIEGG Fluxus article](https://antiegg.kr/25502/)

Initial corpus-size and API observations are hypotheses. Bounded public
requests must verify them. A `403`, robots restriction, rate limit, login
requirement, unclear rights status, or changed API produces a durable blocked
result; it is never an invitation to bypass access controls.

## Privacy and content boundary

Allowed in Git and GitHub:

- public URLs and factual metadata;
- schemas, code, synthetic fixtures, tests, and issue specifications;
- source structure, counts, MIME types, byte sizes, hashes, and rights states;
- sanitized aggregate evidence and high-level technical decisions.

Forbidden in Git, GitHub, logs, screenshots, fixtures, and evidence:

- personal information, chat excerpts, meeting notes, or contributor duties;
- local absolute paths or references to private attachments;
- privately supplied documents or downloaded source PDFs;
- raw article prose, HTML bodies, images, audio, video, captions, transcripts,
  embeddings, or other corpus content;
- credentials, access tokens, cookies, signed URLs, account identifiers, or
  secret values.

Future artist submissions, visitor inputs, generated scores, performer
annotations or choices, and visual-system state or history use the separate
[project-native lifecycle contract](project-native-lifecycle.md). That
synthetic-only contract requires pseudonymous IDs, explicit consent, finite
retention, subject export and withdrawal, exact derivative deletion, and
scoped expiring legal holds. It does not authorize or deploy real intake.

## Durable workflow

Each asset moves through an explicit state machine:

```text
discovered
→ metadata_verified
→ approved_for_ingest
→ transfer_pending
→ raw_in_object_store
→ extraction_pending
→ extracting
→ derived_in_object_store
→ indexed

blocked | failed_retryable | failed_final
```

The ledger records stable source and asset IDs, public source URL, rights state,
media type, byte size, hash, immutable object key, attempt count, lease,
checkpoint, and sanitized evidence references. Local disk is disposable cache.

## Transit lanes

| Lane | Responsibility | Typical runner |
|---|---|---|
| `portable` | schemas, parsers, tests, fixtures, queue logic | CI or coding agent |
| `network-acquisition` | bounded public discovery and HTTP probes | trusted VM |
| `trusted-vm` | persistent acquisition, hashing, approved R2 upload | outbound-paired VM |
| `trusted-laptop` | OCR, transcription, video understanding | outbound-paired laptop |
| `object-storage` | immutable raw/derived objects and manifests | Cloudflare R2 |
| `deploy` | later hosted services | reviewed publisher |

Workers connect outbound over HTTPS, advertise capabilities, claim expiring
leases, heartbeat, checkpoint, and release work on disconnect. No inbound access
to a laptop is required. Queue messages contain identifiers and object keys,
never media or credentials.

## Execution policy

- Use red/green/refactor TDD and small focused commits.
- Keep GitHub issues and PRs as the canonical work ledger.
- Keep rights, privacy, exact-head, evidence, and secret boundaries fail-closed.
- Model/effort racing is disabled for this project and cannot block useful
  work. Candidate comparison remains an optional future policy branch.
- Cloudflare Queues are optional after the local durable queue is proven.
- The first external proof is metadata-only. A later R2 proof is limited to one
  explicitly approved small public object and records retention or cleanup.

## First usable slice

- A minimal Python package and CLI.
- Versioned source, asset, rights, job, lease, object, and evidence schemas.
- State-transition, idempotency, redaction, URL allowlist, rate-limit, retry,
  and restart/resume tests.
- Deterministic fixture discovery that emits a sanitized manifest.
- Bounded public metadata discovery for one source without storing bodies.
- R2 readiness that reports secret names and presence only, and fails closed.
- Progress reconstruction from the durable ledger after restart.

Deployment, model racing, OCR, transcription, video understanding, and
full-corpus downloads are outside this first slice.
