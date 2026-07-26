# NJP Center `mediaObjects` fragment shape

Reviewed on 2026-07-26, the unauthenticated endpoint
`https://njp.ggcf.kr/mediaObjects/more?page=<n>` returns an HTML fragment.
The bound factual item shape is one anchor whose `href` is exactly
`/mediaObjects/<positive integer>` and whose decoded, trimmed text is the item
title. The adapter retains only that public identifier, the canonical
`https://njp.ggcf.kr/mediaObjects/<id>` detail URL, and the title. It does not
retain response bodies, images, iframes, attachment locators, or other markup.

Pages 1–3 were observed with eight unique items each, page 4 with five, and
page 5 with none. The empty fragment retains its pagination wrapper but has no
item container or non-whitespace text. The first valid zero-item page is the
terminal page and is requested once. More than eight item anchors, duplicate
identifiers, a malformed identifier, a nonempty fragment with no matching
anchor, an unexpected MIME type, or an off-host/path response fails closed.

The endpoint did not require a CSRF value at review time. If that changes, the
inventory must stop rather than replaying the parent page token. `robots.txt`
disallows `/attachment/` and `/storage/upload/`; neither prefix is admitted by
the request builder or preflight transport.

The similarly named `/exhibitions/more` and `/articles/more` routes are not
bound by this adapter. A metadata-only check on 2026-07-26 found no matching
`/mediaObjects/<id>` anchors in their first fragments, so their shape remains a
separate review decision.
