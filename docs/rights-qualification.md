# Operation-specific asset qualification

The complete metadata universe and the downloadable or usable rich corpus are
different sets. A public page, official channel, downloadable response, or
metadata approval never supplies content rights by itself. The portable
qualification compiler evaluates each stable source/asset pair separately for
exactly nine operations:

1. metadata retention;
2. download;
3. raw storage;
4. OCR;
5. transcription;
6. video understanding;
7. indexing;
8. score generation; and
9. public retrieval.

An approval for one operation does not imply another. Every approved operation
binds an asset-specific or reviewed source-policy scope, factual basis,
authority class, sanitized evidence reference, decision and expiry times,
review trigger, exact asset-facts hash, and retention class. The asset-facts
hash covers the stable IDs, exact public HTTPS URL and expected host, MIME,
maximum bytes, access state, retention/deletion/derivative/retrieval policies,
and any exact immutable raw-object key. Missing approval fields affect only
that operation and leave it pending. Changed facts, expired authority,
revocation, conflicting decisions, or a retention mismatch fail closed.

## Source boundary matrix

This is a contract matrix, not a live inventory count or a rights decision.
The checked-in repository contains no approved source object, prose, caption,
or media.

| Source family | Metadata qualification | Content qualification |
| --- | --- | --- |
| NJP Video Library | May be evaluated from current reviewed metadata policy | Attachment/media operations remain blocked after 401/403, login, signed/expired URL, unclear permission, or any missing asset-specific decision |
| NJP Center main site | May be evaluated from current reviewed metadata policy | Attachments remain operation-specific; public presentation and a stable URL do not authorize download or derivatives |
| NJP video archive page | Descriptive catalogue facts remain distinct from analogue-tape counts | The page does not imply that tapes are digitized, downloadable, or usable |
| Official NJP YouTube | Official API metadata is a separate operation | Caption/media operations require current platform authority plus an explicit asset-specific lawful basis |
| ANTIEGG | Editorial metadata can remain inventory-only | Prose/media require permission or a clearly reviewed lawful basis; public visibility is never sufficient |
| Project-native families | Not compiled through this external-asset path | Existing consent, privacy, retention, deletion, and withdrawal contracts remain authoritative |

## Current aggregate coverage

No real candidate is promoted by this portable issue. Until current reviewed
asset decisions are compiled from the durable metadata ledger, every source
content operation remains pending or blocked.
Counts are deliberately reported as unknown rather than copied from an
unverified hypothesis. The first live
one-object proof remains separately gated by its exact approval record,
robots/access checks, trusted-VM verification, and delete-after-verification
plan.

The runtime detects duplicate source/asset candidates before work creation and
re-resolves the complete current qualification through an authority boundary.
Object-backed downstream jobs carry only the qualification ID, source ID,
asset ID, operation, and exact immutable R2 object key. They never carry source
bytes, public or signed URLs, credentials, cookies, headers, response bodies,
source prose, private material, or machine-local paths. A changed URL or any
other fact produces a different current qualification and invalidates old
work.

This contract is portable and offline. It makes no network request, reads no
secret, touches no R2 object, and grants no acquisition or deletion authority.
