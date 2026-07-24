# Official YouTube metadata adapter

The portable adapter for `njp-youtube-official` models only the documented
YouTube Data API v3 metadata path:

1. resolve the exact `@NamJunePaikArtCenter` handle with `channels.list`;
2. read its uploads-playlist identifier from
   `contentDetails.relatedPlaylists.uploads`;
3. enumerate stable video identifiers with bounded `playlistItems.list`
   pages; and
4. enrich batches of at most 50 identifiers with factual duration and
   availability observations from `videos.list`.

The checked-in API contract is based on the official documentation for
[`channels.list`](https://developers.google.com/youtube/v3/docs/channels/list),
[`playlistItems.list`](https://developers.google.com/youtube/v3/docs/playlistItems/list),
[`videos.list`](https://developers.google.com/youtube/v3/docs/videos/list),
and the [Data API error model](https://developers.google.com/youtube/v3/docs/errors).
The current documented quota cost of each modeled list call is one unit. That
observation must be revalidated before a live run.

This implementation is offline-only. It does not execute HTTP, read an API
key, use rendered YouTube pages, call `search.list`, use cookies or browser
state, or expose caption, transcript, thumbnail, audio, or video download
methods. The official handle remains unverified by a live observation.
Endpoint governance for the handle and all three API methods remains
`unknown`/`pending`, so no live API request is authorized by this change.

## Pagination and quota boundary

Uploads use the documented opaque `nextPageToken` and a 50-result page cap.
The raw token is stored only inside an integrity-bound local checkpoint.
Public manifests retain a SHA-256 digest, never the token. The local cursor
also binds a monotonic page ordinal; repeated raw tokens and skipped ordinals
fail closed. Checkpoint resume requires externally supplied expected bounds
and digest.

`YouTubeQuotaLedger` accounts for the three reviewed list methods and refuses
an unknown method or a reservation beyond its configured unit budget. Its
checkpoint binds the maximum, consumed units, and per-method counters.
`quotaExceeded` and `dailyLimitExceeded` are durable quota blockers, not
signals to switch accounts, projects, credentials, or endpoints.

## Completeness and asset boundary

Missing requested video identifiers are recorded as
`availability_unavailable`; that is a partial metadata observation, not proof
that a source record was deleted. Public, unlisted, private, region-restricted,
and age-gated observations are reduced to bounded enums. Titles, descriptions,
tags, thumbnails, URLs, prose, and source payloads are not retained.

Caption, thumbnail, audio, and video entries are only non-acquirable candidate
types with `rights_state = pending`. Metadata permission never implies asset
permission. A separate reviewed issue must establish current platform terms,
access/authentication, copyright or lawful basis, retention, quota, cost, and
exact run bounds before any live metadata proof. Captions or media additionally
require an explicit asset-level rights decision and an acquisition worker that
does not exist in this adapter.

## Operator hold

Keep the live lane held until every endpoint-specific governance record needed
by the proposed run is current and explicitly approves `metadata_inventory`.
If a key is later approved, inject it only at the trusted-VM transport boundary;
do not add it to `MetadataRequest`, a URL, checkpoint, manifest, issue, log,
fixture, or commit. Any `401`, `403`, `429`, quota response, shape drift,
redirect, or ambiguous handle resolution stops only this source lane and
preserves resumable state.

