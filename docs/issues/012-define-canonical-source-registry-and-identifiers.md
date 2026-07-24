# Define the canonical source registry and normalized identifiers

depends-on: 001,003

## Goal

Create a versioned, deterministic registry for the complete currently approved source universe. Give each logical source and discovery endpoint a stable normalized identifier without claiming unverified counts, access, rights, or completeness.

## Acceptance tests

- Add strict versioned source-registry and source-endpoint contracts for these logical sources: Nam June Paik Art Center Video Library, Nam June Paik Art Center main site, Nam June Paik Art Center Video Archive page, official Nam June Paik Art Center YouTube channel, ANTIEGG Fluxus context, and future project-native records.
- Assign stable slug-like source IDs independent of titles, redirects, mutable platform handles, query parameters, machine paths, or catalogue counts. Record aliases separately and reject alias collisions.
- Represent the ANTIEGG article, public sitemap, and allowed WordPress metadata APIs as endpoints under one editorial source rather than as evidence of separate holdings.
- Represent the NJP main site and Video Archive page as distinct logical sources sharing a normalized host policy; do not collapse the separate Video Library host into either source.
- Record the official YouTube channel through a stable channel identifier field only after bounded metadata confirms it; until then retain the public handle URL as an unverified locator.
- Define project-native source families for artist submissions, visitor inputs, generated scores, performer annotations or choices, and visual-system state or history without creating any private records.
- Add deterministic normalization, collision, alias, URL canonicalization, and migration tests using invented metadata. Repeated registry generation is byte-identical.
- Reject later public sources unless a reviewed registry change explicitly adds them; an unknown source ID fails closed before network or storage work.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None. The registry contains public locators, stable IDs, and policy state only.

## Artifact outputs

- New versioned source-registry schema under `schemas/`
- New checked-in canonical registry under `config/` or `data/registry/`
- New normalization and collision logic under `src/performing_fire_corpus/`
- New synthetic registry fixtures and tests under `tests/`

## Stop conditions

- Stop if an identifier embeds a person’s name, account identifier, credential, signed value, local path, source prose, or inferred inventory count.
- Stop if two logical sources cannot be distinguished deterministically without an unverified platform fact.
- Stop if a later source is added without explicit approval or if normalization silently rewrites an existing stable ID.

## Human clarification protocol

Ask only if two approved logical sources remain genuinely indistinguishable after host, canonical public URL, source class, and reviewed aliases are considered. Show the proposed stable IDs, recommend preserving separate records, and provide a free-form alternative.

## Recommended response

Use immutable semantic IDs such as `njp-center-main`, `njp-center-video-archive`, `njp-video-library`, `njp-youtube-official`, `antiegg-fluxus`, and namespaced project-native source families. Treat all counts and platform-internal identifiers as nullable observations with evidence timestamps.

## Trade-offs

Semantic IDs are easy to audit but require explicit alias migrations when public locators change. Keeping logical sources separate creates more records while preserving provenance and per-source policy decisions.

## Free-form response

Optional maintainer notes or alternate stable-ID proposal:
