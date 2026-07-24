# Deliberate rich-corpus selection

The source universe and the selected rich corpus are different products. Every
bounded metadata observation remains countable in the source universe even
when its content is blocked, unavailable, duplicated, out of scope, or
deliberately excluded. Selection never turns a prior inventory hypothesis into
a verified total and never turns the one-object pipeline proof into the corpus
boundary.

The portable implementation is deterministic and fixture-only. It authorizes
no source request, media acquisition, retention, transformation, or public
retrieval.

## Authority before ranking

A candidate is eligible only when all of these current facts are approved:

- operation-specific rights and an unexpired rights snapshot;
- retention;
- privacy or consent;
- downstream transformation;
- a known retrievable state and an observed inventory record.

Missing, pending, blocked, revoked, stale, or unavailable facts produce an
explicit exclusion. Technical quality is considered only after these gates and
declared coverage contribution. Popularity and ease of download are not
accepted selection inputs, so they cannot override authority or coverage.

An object used for pipeline proof is excluded from automatic selection. It may
enter only through the same separately reviewed selection decision required
for an ordinary candidate.

## Versioned records

The v1 contracts are:

- `selection-candidate`: stable source/asset identity, evidence scope, declared
  strata, duplicate cluster, and current authority states;
- `coverage-target`: a versioned, explained minimum for one declared stratum;
- `selection-decision`: a hash-bound include, exclude, or unresolved decision
  with authority, rationale, rights snapshot, expiry, policy version, and
  review trigger;
- `selection-exclusion`: a non-destructive explanation linked to its decision;
- `selection-manifest`: the exact inventory snapshot, policy version,
  decisions, exclusions, coverage results, unresolved metadata, and honest
  universe counts.

Safe strata are source, period, language, medium, topic, and performance
context. The evaluator does not infer missing strata with a model. Unknown
metadata stays visible as an unresolved candidate and may leave a declared
target underrepresented.

## Deterministic selection

Targets are evaluated in stable priority and identifier order. Candidates that
pass authority gates are ranked first by contribution to unmet declared
targets, then by technical quality, then by stable candidate ID. A duplicate
cluster keeps every source record but selects at most one representative.
Remaining eligible records are explicitly excluded as `coverage_not_needed`;
they are not silently discarded.

The manifest reports observed, eligible, selected, and shortfall counts for
each target. A target with a shortfall is `underrepresented`. Blocked coverage
is a result, not permission to substitute an easier download, bypass access
controls, or weaken rights and consent.

Changing the inventory snapshot, policy version, candidate facts, targets, or
decision facts changes the bound identifiers. Identical inputs produce
identical bytes and decisions, including deterministic tie handling.

## Review boundary

Every decision has a sanitized authority class, rationale, UTC timestamp,
expiry, review trigger, evidence scope, and policy version. Re-evaluation is
required when inventory evidence, rights, retention, privacy, transformation
eligibility, duplicate evidence, coverage priorities, or policy versions
change.

Selection manifests queue only stable IDs and reviewed object identifiers for
later workers. They contain no proposal material, personal identities, local
paths, credentials, source prose, media, or unreviewed model output.
