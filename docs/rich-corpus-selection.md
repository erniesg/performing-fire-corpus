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

- canonical source governance;
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
enter only when a separate `selection-review-override` is approved, current,
and content-bound to the exact candidate digest. Changing any candidate or
authority fact invalidates that override.

## Versioned records

The v1 contracts are:

- `selection-candidate`: stable source/asset identity, evidence scope, declared
  strata, duplicate cluster, and current authority states. It content-binds
  the exact inventory observation and snapshot plus source-governance, rights,
  retention, privacy, and transformation snapshot hashes and expiries. It is a
  compiled input from those authorities, not a way to manufacture approval;
- `selection-review-override`: a separately reviewed, expiring, hash-bound
  exception that can make one exact pipeline-proof candidate eligible;
- `coverage-target`: a versioned, explained minimum for one declared stratum;
- `selection-decision`: a hash-bound include, exclude, or unresolved decision
  with the full candidate digest, authority, rationale, rights snapshot,
  expiry, policy version, and review trigger;
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
pass authority gates are ranked first by their best unmet target priority,
then by the number of unmet targets they cover, technical quality, and stable
candidate ID. A candidate that covers multiple lower-priority targets can
never outweigh a candidate covering a higher-priority target. A duplicate
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
Candidate construction accepts no caller-supplied authority states. A trusted
resolver must compile the complete inventory, registry/governance, rights,
retention, privacy/consent, and transformation snapshot fields before the
candidate digest is created. Manifest validation recomputes selection,
coverage, identity uniqueness, review-override bindings, and universe
accounting from the embedded content-bound records. It also requires each
exclusion to match its exact decision, so recomputing only the outer hash
cannot falsify those facts. An empty observed universe remains a valid,
explicit zero-count manifest rather than an exception.

## Review boundary

Every decision has a sanitized authority class, rationale, UTC timestamp,
expiry, review trigger, evidence scope, and policy version. Re-evaluation is
required when inventory evidence, rights, retention, privacy, transformation
eligibility, duplicate evidence, coverage priorities, or policy versions
change.
An include decision and its manifest expire no later than the earliest
underlying source-governance, rights, retention, privacy, or transformation
authority. Expired and unresolved authority can only produce a non-authorizing
exclusion or unresolved decision.

Selection manifests queue only stable IDs and reviewed object identifiers for
later workers. They contain no proposal material, personal identities, local
paths, credentials, source prose, media, or unreviewed model output.
