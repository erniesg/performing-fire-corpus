# NJP Video Library bounded inventory

## Current coverage

The issue 31 trusted-VM run is **blocked before catalogue access**. The run
started from the exact clean commit
`fcda3289f261687bd84a43e9bcf5f7bb26d5d8f6` on
`codex/issue-31-rucksack`. It made one unauthenticated metadata-policy request
and no catalogue, playback, stream-manifest, caption, thumbnail, image,
document, attachment, or signed-locator request.

The required robots request returned status `200`, MIME type `text/html`, and
6,566 bytes. Its safe response digest is
`c65e0542a1604ce8bb98bcf4981ba5ca76e5c7b784444653a6a3d12a1ba9f094`.
Because a robots policy was not served with the expected MIME type, the result
is the durable blocker `robots_ambiguous`. The run stopped without interpreting
the HTML response or trying another endpoint, cookie, token, referer, browser,
or authentication path.

## Bounds and policy snapshot

The prerequisite request had these hard limits:

- one total request and zero catalogue pages;
- 65,536 bytes per response and in aggregate;
- zero retries;
- a two-second host interval if another request had been eligible;
- a five-second connection timeout and ten-second total elapsed timeout.

The source adapter was `njp-video-library-html` version `1.0.0`. Robots is
`ambiguous`; access control, authentication, API availability, platform terms,
and copyright or lawful basis remain `unknown`. Metadata inventory and
retention remain `pending`. These facts do not authorize a catalogue request,
so no source-shape assumption from the offline invented fixtures was promoted
to a live fact.

## Sanitized aggregate result

| Measure | Result |
|---|---:|
| Unique stable records | 0 |
| Language aliases | 0 |
| Duplicate records | 0 |
| Rejected unsafe fields | 0 |
| Candidate media relationships | 0 |
| Catalogue requests | 0 |
| Durable blockers | 1 (`robots_ambiguous`) |
| Bounded unvisited remainder | All catalogue pages |
| Coverage status | `blocked_before_catalogue` |

These are Video Library-only counts. They are not combined with the NJP main
site, Video Archive, or YouTube.

## Resume and idempotency

The ignored local checkpoint preserves the adapter version, exact checkout,
policy snapshot, bounds, request facts, and terminal blocker. Pagination was
never available because the stop occurred before a catalogue response, so a
page-checkpoint resume demonstration is not applicable. The blocker is
terminal and `resume_allowed` is false; an idempotent rerun must make no
request and preserve the zero-record aggregates. Raw response material,
headers, request facts, checkpoint, and manifest remain under the ignored
`.local/network-smoke/issue-31/` state and are not committed.

The next safe action is to preserve this blocker. A maintainer may separately
review a different public metadata endpoint or explicitly authorize a
browser-authenticated trusted-VM lane. This result does not authorize either
action and does not authorize media acquisition.
