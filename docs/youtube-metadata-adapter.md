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
and digest. Because the provider defines the token as opaque, its local value
is validated by the declared character/length shape rather than semantic word
scanning; all non-cursor checkpoint state remains redaction-validated.

`YouTubeMetadataCoordinator` requires an operator-supplied SQLite-backed
`YouTubeQuotaStore` and owns one run-bound quota ledger. Every coordinator for
the same run must use that durable authority; a new coordinator or reopened
database connection observes the already-consumed units instead of minting a
fresh budget. The database creates a random, non-secret authority identifier
with the run and binds every reservation and checkpoint restore to it. A
checkpoint copied into a fresh database therefore cannot recreate the same
budget. Reservations use an immediate SQLite transaction, so separate
coordinators cannot each spend the final unit. The coordinator exposes only an
immutable quota snapshot.

The same authority stores canonical channel-resolution and uploads-inventory
artifacts only after their coordinator-owned stages finalize. A restarted
coordinator can reload those issued artifacts and resume the next stage without
spending another channel-resolution unit. A caller-created artifact, an
artifact from a fresh store, or a conflicting artifact for the same run cannot
authorize a downstream request.

The coordinator is the only normal constructor for all three stages. Every
request builder reserves its reviewed method cost before returning a request;
a request cannot be built without the ledger. The common checkpoint stores the
run ID, maximum, consumed units, and per-method counters inside its outer
integrity binding, and restores them before a retry. A restore may only advance
those counters; an older stage checkpoint cannot rewind a shared run ledger.
The database location and connection never enter a checkpoint, manifest,
request, fixture, issue, or log. Local exhaustion stops the harness as
`quota_exhausted` before a request is returned. Provider quota reasons are
reduced at the transport boundary to the same body-free blocker.
`quotaExceeded` and `dailyLimitExceeded` never signal permission to switch
accounts, projects, credentials, or endpoints.
Documented `rateLimitExceeded` and `userRateLimitExceeded` responses are
separate transient `rate_limited` blockers, not access denials.

Channel resolution produces a non-publicly-constructible, integrity-bound
artifact. The coordinator owns the channel harness as well as the uploads
harness: it issues the exact-handle request, accounts for its quota unit,
accepts the response, and finalizes only a complete one-record terminal
manifest. The public uploads lifecycle accepts only the exact channel
resolution issued by that coordinator, so directly invoking a parser cannot
authorize an uploads request. Callers can request the next bounded request,
submit a response, record a retry, or obtain a checkpoint, but finalization
accepts no caller-supplied harness or manifest. Only the coordinator's exact
adapter/session/resolution and a complete terminal result with no rejected
records can issue an uploads inventory; every record identity digest is
rechecked against its video ID.
`videos.list` accepts only a sorted subset of that inventory, so arbitrary
public-looking video IDs cannot enter enrichment. Every harness checkpoint
also binds the adapter-lineage digest, preventing resume under another channel
or uploads inventory. Platform playlist and video IDs are validated as opaque
Base64url-compatible identifiers, not scanned for accidental word fragments;
stable record IDs use a reversible canonical ASCII-hex encoding, while
normalized source identities use a fixed safe-prefixed digest. Valid IDs
beginning with `-` or `_`, or containing account-like random fragments, remain
representable without exceeding shared identity or redaction bounds.

## Completeness and asset boundary

Missing requested video identifiers are recorded as
`availability_unavailable`; that is a partial metadata observation, not proof
that a source record was deleted. Public, unlisted, private, region-restricted,
and age-gated observations are reduced to bounded enums. Live items are
separately classified as upcoming, live, or completed from the reviewed
`liveStreamingDetails` part; absent details are `not_live`, and an unknown
lifecycle shape fails closed. UTC source timestamps accept the documented
RFC 3339 form with optional fractional seconds while rejecting impossible
calendar values. Region restrictions must use exactly one
documented country-code list, and non-object or unknown YouTube age-rating
shapes also fail closed instead of defaulting public. Titles, descriptions,
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
