# Project-native consent and lifecycle contract

This contract defines future handling for artist submissions, visitor inputs,
generated scores, performer annotations or choices, and visual-system state or
history. It is implemented and tested with invented records only. It does not deploy an intake surface,
authorize collection, or contain any proposal,
meeting, participant, or source content.

## Data minimization

Every contribution has a stable pseudonymous contribution ID. Direct
participant contributions also carry one pseudonymous subject reference and
one current consent ID. Generated scores and visual-system records carry the
stable IDs of every input plus an independent system-provenance ID; they do not
copy a participant identity into the derived record.

The durable record contains only:

- data class, purpose, consent notice and state;
- confidentiality, allowed audiences, and exact allowed operations;
- stable provenance and input-contribution IDs;
- exact immutable raw and derived object keys;
- exact index, cache, and score-export IDs;
- retention expiry, withdrawal, deletion, export, and legal-hold state; and
- a creation timestamp.

Names, contact details, team duties, signatures, private proposal or meeting
text, free-form comments, credentials, local paths, and content bytes are not
fields in any schema. Unknown fields fail validation.

## Admission and use

Participant-originated records require affirmative, current, specific,
withdrawable consent. The contribution source, pseudonymous subject, purpose,
notice version, confidentiality, audiences, uses, and retention must remain
within that consent. Silence, a pending record, a prechecked default, or broad
future-use language is not authority. Consent must already be effective when
the contribution is created and when a subject export is requested; a future
decision time or an expired authority fails closed.

Each use is evaluated again against current consent, retention, deletion,
audience, redaction, and contribution state. A stale consent snapshot, changed
purpose, stricter confidentiality, shortened retention, expiry, withdrawal,
pending deletion, unauthorized audience, or missing redaction blocks use.
Public retrieval additionally requires a public contribution and the public
audience. Subject export requires both `subject_copy` policy and the explicit
`subject_export` consent operation. It traverses the authoritative graph and
includes subject-only derivatives; a mixed-subject derivative is blocked
rather than disclosed.

The project-native operations are metadata inventory, derived processing,
indexing, score generation, search visibility, retention, subject export, and
public retrieval. A permission for one never implies another.

## Derived records

Generated scores and visual-system state or history inherit:

- the intersection of input uses and audiences;
- the most restrictive input confidentiality;
- the earliest input retention expiry;
- every input consent and contribution ID; and
- an independent system-provenance ID.

An incompatible purpose, empty authority intersection, expired or withdrawn
input, missing input, or missing system provenance prevents creation. A
derived record cannot make an input more public or retain it longer.
Every derived use traverses the complete input graph again, requires each
current consent/retention/deletion bundle, and recomputes purpose, consent
lineage, audiences, uses, confidentiality, and retention. The caller must
supply a versioned, creator-issued, ID- and hash-bound lineage snapshot that
independently records the complete contribution universe, input IDs, consent
IDs, and system provenance. The lifecycle engine confirms exact issuance
through a trusted durable-authority resolver; a structurally valid
caller-created snapshot is not authority. A missing inventory member, altered
or unissued snapshot, changed input or consent set, cycle, stale inherited
field, or later input withdrawal blocks the derived operation.

## Withdrawal, deletion, and legal hold

Withdrawal removes allowed uses from every direct or derived record linked to
the revoked consent, using authoritative transitive graph traversal, and moves
each record to pending deletion. The deletion plan contains exact targets for
raw objects, derived objects, index documents, caches, and score exports. It
carries stable IDs and object keys only.

Completion requires exact equality between every planned and observed removal
set and rebinds every plan target to the current authoritative contribution
inventory before any completion can be attested. A self-consistent but
re-targeted work record, missing contribution, or extra key fails closed.
Successful completion emits one content-free tombstone per contribution with
IDs, completion time, and counts; it contains no deleted content or locator.

A legal hold is a separate record with reviewed authority and basis, an exact
contribution scope, decision time, mandatory review time, and expiry. A missing,
partial, review-due, released, or expired hold cannot authorize a held state.
A current scoped hold produces review work and prevents automatic completion.
The deletion authority status and supplied hold must agree exactly. Completion
also requires a separately issued, hash-bound legal-hold resolution for the
exact contribution scope. The resolution is valid for at most five minutes
and is fetched through the trusted current-authority resolver immediately
before completion. It must still be current at completion; a newly issued hold
blocks older pending work. A missing or unavailable resolver fails closed. A
hold status without its reviewed hold record cannot fall through to ordinary
pending deletion. A hold never restores use eligibility or silently becomes
indefinite retention.

## Retention defaults and product boundary

Every contribution has a finite retention timestamp, clipped to its current
consent and all input authorities. The contract has no indefinite default.
Portable maximum defaults are 90 days for artist submissions and generated
scores, 60 days for performer annotations or choices, and 30 days for visitor
inputs and visual-system state or history. Derived records remain clipped to
the earliest input expiry even when their class default is longer.

The deletion SLA maximum is seven days for artist submissions and 72 hours for
every other data class. A shorter consent or reviewed pilot policy always wins.
Different production durations remain a later privacy decision and may only
shorten these defaults; until then, no real intake is authorized.

The first real project-native pilot remains separately gated by issue #46. It
must supply reviewed notice, consent language, data-class retention, deletion
owner, access control, and a bounded pilot plan before any real record or
content enters the system.
