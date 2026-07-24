# NJP Video Library metadata adapter

The `njp-video-library` adapter preserves the Video Library as a distinct
catalogue on `njpvideo.ggcf.kr`. It is not an extension of the NJP Center main
site, Video Archive page, or official YouTube source, and it does not combine
their counts or identities.

## Production hold

The production adapter is deliberately held. The endpoint-level governance
record remains `unknown` for robots, access, authentication, API
availability, platform terms, and copyright or lawful basis, and every
operation remains `pending`. A production request or parse raises
`SourceShapeUnreviewed`.

The hold can be removed only after a bounded trusted-VM observation records
the current catalogue mechanism and narrow factual projection, together with
reviewed request, page, byte, retry, rate, and elapsed-time bounds. Historical
count, size, media, and HTML-shape hypotheses are not encoded as source facts.
The follow-up inventory must stop on robots ambiguity, access control,
`401`, `403`, login, rate exhaustion, unexpected MIME or structure, signed
locators, or unclear terms and retention.

## Offline contract

Invented HTML fixtures exercise a source-specific seam with live networking
disabled. The retained projection is limited to:

- a stable public catalogue identifier or canonical same-host record URL;
- a bounded record-class enum;
- a language-coverage enum;
- an optional explicitly published year;
- an optional ISO 8601 duration.

Titles, prose, raw HTML, descriptions, media, captions, transcripts, and
machine-local paths are never retained. Mutable display labels and bilingual
aliases do not define identity. Page position and ordering do not define
identity. Canonical URLs must use the exact unauthenticated HTTPS host with no
query, fragment, alternate port, or ambiguous path.

The adapter inherits the shared conformance matrix for budgets, robots
denial, access blockers, MIME and byte limits, pagination loops, ordinal
drift, expected-total drift, retry and checkpoint integrity, duplicates,
stable-ID collisions, deterministic ordering, forbidden fields, and network
denial.

Passing the offline suite proves only the fail-closed adapter contract. It
does not claim that invented attributes match the current source and does not
authorize a live request.

## Asset boundary

Video, caption, thumbnail, image, and document locators are represented only
as relationship candidates. Every candidate begins with `pending` rights,
`acquisition_eligible = false`, and `retry_allowed = false`. The adapter has
no asset request, fetch, or download method.

Candidate paths are unusable until a bounded source-shape review supplies an
exact path policy. Synthetic tests override that empty policy with an
invented path prefix. Off-host, credentialed, signed, alternate-port,
ambiguous, and unreviewed-path locators fail closed. A later exact `401`,
`403`, or `429` observation can make the matching candidate durably blocked;
it cannot enable a retry or acquisition.

Asset bytes, caption bytes, thumbnails, documents, source prose, and media
remain outside this portable issue. They require separate operation-specific
rights and access decisions.
