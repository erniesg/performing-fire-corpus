# Operator gates: actionable blockers and resumable state

A blocker is first-class durable state, not a stalled process.
`performing_fire_corpus.operator_gates` is the only place a human gate is
opened, decided, or resumed.

## Every gate is actionable

An `operator_blocker` record is invalid unless it carries all of:

- the missing authority class
- one privacy-safe question
- the recommended response, which is always the least permissive one
- the exact next safe action
- the unblocking command class
- a review trigger
- an expiry
- a durable resumable checkpoint, referenced by its resume token

`BLOCKER_CATALOG` holds one entry per authority class, and every field in it is
fixed literal text. Nothing is interpolated from a provider response, a source
page, or a person, so a blocker is always safe to write to a log, an issue, or
an evidence manifest.

The recognized authority classes are source governance, rights, retention,
privacy, transformation, object-storage authority, Actions spending authority,
network acquisition, trusted-laptop pairing, and deploy approval.

## One blocked job does not hold unrelated work

Every blocker declares an isolation scope: `single_job`, `single_endpoint`,
`single_source`, or `single_worker`. `partition_work` splits the queue into what
one blocker holds and what stays runnable. A blocked endpoint blocks that
endpoint only; work on other endpoints, sources, and workers keeps moving.

## Decisions are least permissive by default

A `human_decision` is `granted`, `denied`, or `deferred`.

- A grant must name exactly the missing authority class and must expire. It
  cannot widen the gate and it cannot outlive its own decision time.
- A denial records no authority and drops the resume reference. It fails closed.
- A deferral records no authority but keeps the resume reference, so the work
  can continue once a later decision exists.
- An expired blocker cannot be granted. It must be re-opened first; the only
  decision an expired blocker accepts is a deferral.

## Resumable state

`build_resume_token` commits the checkpoint that the blocked work will restart
from: cursor, next ordinal, processed count, last stable ID, and attempt. The
checkpoint is bound by its own digest.

`resume_checkpoint` returns that checkpoint only when the decision is a grant,
the decision belongs to the same resume reference, the digest still matches, the
resume reference has not expired, and the granted authority has not expired.
Any other state raises instead of resuming.

## Human clarification protocol

Ask only when a real blocker lacks the authority needed for its exact next safe
action. Use stable IDs and sanitized codes, recommend the least permissive
response, include the resumable checkpoint, and never request secret values or
protected material. A question is drawn from the catalog, so it can never carry
source prose, personal details, or a provider payload.

Resolving a human gate is not merge approval.

## Rucksack defects are routed, not implemented

A defect or improvement discovered in the Rucksack product itself is filed as a
separate privacy-safe issue in `erniesg/rucksack`. It is never fixed inside this
corpus repository and never mixed into a corpus feature change.

See `docs/safe-observability-and-evidence.md` for the observability and evidence
half of this contract.
