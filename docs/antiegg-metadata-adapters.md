# ANTIEGG public metadata adapters

`antiegg-fluxus` is secondary Korean editorial and Fluxus context, not the
primary corpus. These two adapters replace the earlier article-body-shaped
discovery assumption with a bounded, prose-free metadata projection over the
already registered public endpoints:

| Adapter | Endpoint | Canonical URL |
|---|---|---|
| `antiegg-sitemap-xml` | `antiegg-sitemap` | `https://antiegg.kr/sitemap_index.xml` |
| `antiegg-posts-metadata-json` | `antiegg-posts-api` | `https://antiegg.kr/wp-json/wp/v2/posts` |

## Reviewed shape boundary

The posts adapter is shape-bound to the public WordPress REST v2 response
reviewed on 2026-07-26. Requests always include canonical `page` and
`per_page=2` controls plus an exact `_fields` projection; the reviewed
WordPress maximum is 100, so the adapter cannot request above it. Pagination
uses sanitized `x-wp-total` and `x-wp-totalpages` response headers. The checked
fixture inventory reaches two stable records and terminates with no unvisited
remainder. The live endpoint declared 1,463 records when reviewed; that header
is an observation, not a claim that the offline fixture reached the live
corpus.

The sitemap registry entry now uses the advertised `sitemap_index.xml`.
A sanitized fixture records that the former `wp-sitemap.xml` locator returned
`301` to that endpoint. The sitemap adapter itself remains held because its
document and pagination shape are outside this review.

## Retained projection

Nothing outside the approved projection is retained. Excerpts, rendered or raw
prose, HTML, embedded media, author or contact details, comments, captions,
and response bodies never enter a record, and no adapter exposes a body,
download, or asset-request entry point.

Sitemap entries retain:

- `entry_kind` — `entry_kind_public_document` for a `<urlset>`,
  `entry_kind_child_sitemap` for a `<sitemapindex>`;
- `modified_at` — `<lastmod>`, and only as a full UTC instant.

The response projection validates these reviewed fields before admitting an
item: numeric `id`; local `date` and `modified`; `slug` and canonical `link`;
the rendered title and excerpt containers; numeric `author` and
`featured_media`; and numeric `categories` and `tags` arrays. Missing `id` or
`link`, an added `content` field, or any changed field type fails closed.

WordPress post records retain only `record_type_post` plus hashed identity.
The rendered title and excerpt are validated transiently but not copied into
the bounded record. `content` is absent from `_fields`, is never requested,
and is rejected if it appears. No author, category, tag, featured-media, slug,
link, local timestamp, HTML, or response body is retained. Accordingly
`prose_retention` and `media_acquisition` remain `pending`.

## Identity

Posts identity comes from the canonical source ID plus the immutable numeric
WordPress `id`; changing either title or link does not change it. Sitemap
identity continues to use its canonical public URL. A canonical URL must be an
unauthenticated `https://antiegg.kr` URL with no credentials, port, query,
fragment, percent-escape, relative segment, or ambiguous separator; a trailing
slash is normalised away.

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
- a `<lastmod>` that is not a full UTC instant, or a WordPress local date that
  is not a canonical local ISO-8601 timestamp;
- any XML doctype, entity, CDATA section, extra processing instruction,
  unknown element or attribute, or stray text node in a sitemap document;
- an absent or malformed reviewed WordPress field;
- a missing, non-canonical, or inconsistent WordPress pagination header.

A missing or stale endpoint decision blocks that endpoint only. An
`antiegg-sitemap` blocker leaves `antiegg-posts-api` and every other source
untouched, and one endpoint's approval never authorises another.

## Offline evidence

The sitemap adapter inherits the standard conformance matrix (zero budgets,
robots
denial, `401`/`403`/`429`, login and subscription signals, redirects, MIME and
size bounds, shape drift, pagination and ordinal loops, retry/resume
integrity, duplicates, stable-ID collisions, deterministic manifests,
forbidden fields and values, and automatic network denial). The posts tests
cover the reviewed request projection, response headers, numeric
identity, prose exclusion, mutated-field rejection, beyond-end termination,
declared totals as observations, and endpoint decision isolation.

Run them with the repository validation commands:

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```
