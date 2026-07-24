# Official YouTube metadata adapter

The portable adapter for `njp-youtube-official` models only the documented
YouTube Data API v3 metadata path:

1. resolve the exact `@NamJunePaikArtCenter` handle with `channels.list`;
2. read its uploads-playlist identifier from
   `contentDetails.relatedPlaylists.uploads`;
3. enumerate stable video identifiers with bounded `playlistItems.list`
   pages; and
4. enrich inventory-bound batches of at most 50 identifiers with factual
   duration, availability, and live-lifecycle observations from `videos.list`.

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
`www.googleapis.com` is a registry-only locator host and is not in the generic
content-acquisition allowlist. Each adapter declaration binds its exact
documented YouTube API path.

## Pagination and quota boundary

Uploads use the documented opaque `nextPageToken` and a 50-result page cap.
The raw token is stored only inside an integrity-bound local checkpoint.
Public manifests retain a SHA-256 digest, never the token. The local cursor
also binds a monotonic page ordinal; repeated raw tokens and skipped ordinals
fail closed. Checkpoint resume requires externally supplied expected bounds
and digest.

`YouTubeMetadataCoordinator` owns one run-bound quota ledger and is the only
normal constructor for all three stages. Every request builder reserves its
reviewed method cost before returning a request; a request cannot be built
without the ledger. The common checkpoint stores the run ID, maximum,
consumed units, and per-method counters inside its outer integrity binding,
and restores them before a retry. Provider quota reasons are reduced at the
transport boundary to the body-free `quota_exhausted` blocker.
`quotaExceeded` and `dailyLimitExceeded` never signal permission to switch
accounts, projects, credentials, or endpoints.

Channel resolution produces a non-publicly-constructible, integrity-bound
artifact. The uploads manifest carries an adapter-lineage digest bound to that
resolution. Only a complete terminal manifest with no rejected records can
produce an uploads inventory. `videos.list` accepts only a sorted subset of
that inventory, so arbitrary public-looking video IDs cannot enter enrichment.

## Completeness and asset boundary

Missing requested video identifiers are recorded as
`availability_unavailable`; that is a partial metadata observation, not proof
that a source record was deleted. Public, unlisted, private, region-restricted,
and age-gated observations are reduced to bounded enums. Live items are
separately classified as upcoming, live, or completed from the reviewed
`liveStreamingDetails` part; absent details are `not_live`, and an unknown
lifecycle shape fails closed. Titles, descriptions, tags, thumbnails, URLs,
prose, and source payloads are not retained.

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
