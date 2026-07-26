# Bind the ANTIEGG adapter to the live WordPress REST shape

depends-on: 017,027

## Goal

Replace the held ANTIEGG metadata adapter with one bound to the live, reviewed
public shape so a bounded inventory produces real records instead of stopping
on `SourceShapeUnreviewed` or `response_structure_changed`.

The current adapter targets the single article page. The catalogue is served by
the site's public WordPress REST API, which is unauthenticated, robots-allowed,
and carries the whole corpus.

## Reviewed live shape (observed 2026-07-26)

```
GET https://antiegg.kr/wp-json/wp/v2/posts?page=<n>&per_page=<n>
-> 200, content-type application/json
   x-wp-total: 1463
   x-wp-totalpages: <n>
```

Each item carries these factual metadata fields:

- `id` (int, stable record identity)
- `date`, `modified` (ISO-8601 local strings)
- `slug`, `link` (canonical public URL)
- `title.rendered` (HTML-escaped string)
- `excerpt.rendered` (HTML string)
- `author` (int), `featured_media` (int)
- `categories` (int array), `tags` (int array)

robots.txt grants blanket crawl access (`User-agent: *` with an empty
`Disallow:`); `sitemap_index.xml` is advertised. The registry currently records
`wp-sitemap.xml`, which 301s; the advertised path is `sitemap_index.xml`.

## Acceptance tests

- `stable_record_id` derives from the numeric `id`, not the title, and two
  items whose titles differ but whose `id` matches resolve to one identity.
- `build_request` emits `page` and `per_page` within the reviewed bounds and
  never requests `per_page` above the reviewed maximum.
- `parse_page` returns the fields listed above and rejects any response whose
  item is missing `id` or `link`, raising the existing structure-change error
  rather than inventing a record.
- Pagination terminates on `x-wp-totalpages`, and a page beyond the last
  returns zero records without error.
- No prose body is retained: `content` is never requested and never stored.
  `prose_retention` and `media_acquisition` remain `pending` for this source.
- The source registry's sitemap endpoint is corrected to `sitemap_index.xml`
  with a fixture proving the old path redirects.

## TDD sequence

1. Add fixtures for a two-item page and a beyond-the-end empty page.
2. Assert identity, pagination and field extraction against the fixtures.
3. Replace `_require_reviewed_shape` with the bound implementation.
4. Assert the fail-closed path still triggers on a mutated fixture.

## Exact-head definition of done

Bounded inventory against the fixtures yields records with stable ids and no
prose, and the corpus audit reports the ANTIEGG endpoint as shape-bound.

## Validation command

```bash
python3 -m unittest discover -s tests
```

## Allowed secrets

None. This source is unauthenticated.

## Artifact outputs

Updated adapter, fixtures, and a sanitized shape note under `docs/`.

## Stop conditions

Stop if the live API requires a key, returns non-JSON, or if honouring the
reviewed request bounds is impossible. Do not raise the byte or request bounds
to make a page fit.

## Human clarification protocol

If the observed field set differs from the shape recorded above, stop and
report the delta rather than widening the parser to accept both.

## Recommended response

Bind to the documented WordPress REST v2 contract, keep the article-page
adapter as a fallback, and treat metadata only as in scope.

## Trade-offs

Binding to a vendor REST contract couples the adapter to WordPress, but that
contract is versioned and stable, and the alternative — parsing rendered HTML —
is what produced the current `response_structure_changed` failure.

## Free-form response

Report the record count actually reachable and any field that was absent from
real responses despite appearing here.
