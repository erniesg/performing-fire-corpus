# ANTIEGG public metadata adapters

`antiegg-fluxus` is secondary Korean editorial and Fluxus context, not the
primary corpus. These two adapters replace the earlier article-body-shaped
discovery assumption with a bounded, prose-free metadata projection over the
already registered public endpoints:

| Adapter | Endpoint | Canonical URL |
|---|---|---|
| `antiegg-sitemap-xml` | `antiegg-sitemap` | `https://antiegg.kr/wp-sitemap.xml` |
| `antiegg-posts-metadata-json` | `antiegg-posts-api` | `https://antiegg.kr/wp-json/wp/v2/posts` |

## The adapters are held

Both production adapters are deliberately held. `build_request`, `parse_page`,
`detect_access_blocker`, and `declared_total_observation` raise
`SourceShapeUnreviewed` before any work happens. The endpoint governance
records for `antiegg-article`, `antiegg-media-api`, `antiegg-posts-api`, and
`antiegg-sitemap` stay `unknown`/`pending`, so `prose_retention` and
`media_acquisition` remain blocked.

Tests alone enable an invented-fixture seam. Passing these tests is not a
live-source approval, and it is not a claim that antiegg.kr currently serves
these shapes. In particular the bounded pagination controls (a `page` cursor
plus explicit terminal, cursor, ordinal, expected-total, and rejected-count
markers) are invented for the fixtures. WordPress core does not paginate
`wp-sitemap.xml` this way and returns page totals in response headers the
offline harness does not model. A current bounded review must define the real
control shape, and must confirm robots, terms, API availability, access,
authentication, copyright/lawful basis, and retention for each endpoint,
before a single request is emitted.

## Retained projection

Nothing outside the approved projection is retained. Excerpts, rendered or raw
prose, HTML, embedded media, author or contact details, comments, captions,
and response bodies never enter a record, and no adapter exposes a body,
download, or asset-request entry point.

Sitemap entries retain:

- `entry_kind` — `entry_kind_public_document` for a `<urlset>`,
  `entry_kind_child_sitemap` for a `<sitemapindex>`;
- `modified_at` — `<lastmod>`, and only as a full UTC instant.

WordPress post entries retain:

- `record_type` (required) — `record_type_post` or `record_type_page`;
- `format` — the closed WordPress post-format vocabulary;
- `language` — `language_ko`, `language_en`, `language_bilingual`,
  `language_unknown`;
- `published_at` and `modified_at` — `date_gmt`/`modified_gmt` as full UTC
  instants.

`title`, `excerpt`, `content`, `guid`, `author`, `comment_status`, and
`yoast_head` are treated as forbidden even though the API labels the object as
metadata. `status` is read only as an admission gate: anything other than
`publish` fails closed instead of being retained.

## Identity

Identity comes from the canonical source ID plus the canonical public URL, so
one article has one ID no matter which endpoint observed it. The sitemap
`<loc>` for the ANTIEGG Fluxus article and the posts-API `link` for the same
article produce the same record ID and the same source identity, and a changed
display title never changes either. A canonical URL must be an
unauthenticated `https://antiegg.kr` URL with no credentials, port, query,
fragment, percent-escape, relative segment, or ambiguous separator; a trailing
slash is normalised away. The posts adapter additionally requires the
permalink to equal the immutable public post ID, so a link and an ID that
disagree fail closed rather than minting a second identity.

## Completeness honesty

A declared total is only ever an endpoint observation.
`declared_total_observation` returns the declared total together with the UTC
time it was observed, the number of records actually observed, and
`is_completeness_guarantee: false`. Bounded pagination runs through the shared
checkpoint engine, so partial runs report `unvisited_remainder` instead of
claiming a complete inventory.

## Fail-closed boundary

Both adapters stop rather than widen the projection on:

- `401`, `403`, `429`, and login or subscription signals;
- disallowed redirects, unexpected MIME types, and oversized responses;
- shape drift, pagination loops, ordinal mismatches, and non-canonical cursor
  spellings (`page-002` is the only spelling of page two);
- a `<lastmod>`, `date_gmt`, or `modified_gmt` that is not a full UTC instant;
- any XML doctype, entity, CDATA section, extra processing instruction,
  unknown element or attribute, or stray text node in a sitemap document;
- an unreviewed post `status`, `type`, `format`, or language token;
- an envelope, counter, or control marker that is not canonical.

A missing or stale endpoint decision blocks that endpoint only. An
`antiegg-sitemap` blocker leaves `antiegg-posts-api` and every other source
untouched, and one endpoint's approval never authorises another.

## Offline evidence

Both adapters inherit the standard conformance matrix (zero budgets, robots
denial, `401`/`403`/`429`, login and subscription signals, redirects, MIME and
size bounds, shape drift, pagination and ordinal loops, retry/resume
integrity, duplicates, stable-ID collisions, deterministic manifests,
forbidden fields and values, and automatic network denial). The
adapter-specific tests additionally cover cross-endpoint identity agreement,
prose exclusion, XML construct rejection, timestamp canonicalisation, declared
totals as observations, and endpoint decision isolation.

Run them with the repository validation commands:

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```
