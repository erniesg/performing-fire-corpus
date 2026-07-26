# Bind the NJP Center adapter to the live mediaObjects fragment shape

depends-on: 019,029

## Goal

Bind the NJP Art Center adapter to the reviewed live listing shape so the
bounded preflight can progress past `source_shape_unreviewed` and inventory
the video archive.

The archive page renders no items server-side, which is why earlier runs found
only navigation markup. Items are fetched by an ajax call the page itself
declares.

## Reviewed live shape (observed 2026-07-26)

```
GET https://njp.ggcf.kr/mediaObjects/more?page=<n>
-> 200, content-type text/html, ~7.6 KB fragment
```

The page declares this endpoint inline:

```
$.get("https://njp.ggcf.kr/mediaObjects/more?page=2", function (data) { ... })
```

The fragment contains one anchor per item:

- `href="/mediaObjects/<id>"` where `<id>` is a stable integer
- item title text adjacent to the anchor, Korean, HTML-escaped

Observed bounds: page 1, 2 and 3 return 8 unique items each, page 4 returns 5,
and page 5 and beyond return 0. The archive is 29 items and terminates
naturally.

The same `/more?page=` pattern responds on `/exhibitions` and `/articles`.
`/collections/more` returns 500 and is out of scope here.

robots.txt allows both registered paths; only `/attachment/` and
`/storage/upload/` are disallowed, and neither is requested by this adapter.

## Acceptance tests

- `build_request` targets `/mediaObjects/more?page=<n>` and refuses any URL
  outside the reviewed host and path.
- `stable_record_id` derives from the `/mediaObjects/<id>` integer, so a title
  change does not change identity.
- `parse_page` extracts id, canonical detail URL and title, and raises the
  existing structure-change error when the anchor pattern is absent rather than
  returning an empty page as success.
- Pagination stops on the first zero-item page; the terminal page is not
  retried.
- A fixture proves the disallowed `/attachment/` and `/storage/upload/`
  prefixes are never requested.
- No page body, image or attachment is retained; only the listed factual fields.

## TDD sequence

1. Add fragment fixtures for a full page, the short final page, and an empty page.
2. Assert identity, extraction and termination against the fixtures.
3. Replace the held shape guard with the bound implementation.
4. Assert fail-closed behaviour on a mutated fixture.

## Exact-head definition of done

The bounded preflight reports the NJP Center endpoint as shape-bound and the
completeness report lists 29 reachable records with no blocker other than any
that governance itself raises.

## Validation command

```bash
python3 -m unittest discover -s tests
```

## Allowed secrets

None. This source is unauthenticated.

## Artifact outputs

Updated adapter, fixtures, and a sanitized shape note under `docs/`.

## Stop conditions

Stop if the live fragment stops carrying `/mediaObjects/<id>` anchors, if the
endpoint begins requiring the page CSRF token, or if item counts per page
exceed the reviewed bound.

## Human clarification protocol

If `/mediaObjects/more` starts requiring the `csrf-token` meta value that the
parent page carries, stop and report it rather than replaying the token.

## Recommended response

Bind only `/mediaObjects` here. Treat `/exhibitions` and `/articles` as
follow-on work once this shape is proven.

## Trade-offs

An HTML fragment is a weaker contract than a JSON API and can drift silently,
which is why the parser must fail closed on a missing anchor pattern rather
than reporting an empty inventory as success.

## Free-form response

Report the item count actually reached and whether the `/exhibitions` and
`/articles` fragments share the anchor shape.
