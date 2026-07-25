# Source governance and project-native lifecycle

The canonical source registry identifies the known research universe. The
source-governance registry separately records whether a particular operation is
currently eligible. Registration never grants permission.

## Fail-closed source decisions

Each source has explicit facts for access control, API availability,
authentication, copyright or lawful basis, platform terms, and robots. A
non-expired observation must support every fact. Unknown, stale, conflicting,
missing, future-dated, or expired evidence blocks the operation.
Passing states are dimension-specific: API availability cannot stand in for
robots permission, platform permission, or a copyright lawful basis.
Direct evaluation also verifies that an endpoint belongs to its canonical
source. Asset-scoped decisions require an explicit reviewed asset-to-source
binding.

Permissions are operation-specific. Metadata inventory, prose retention,
caption retention, media acquisition, derived processing, indexing, search
visibility, retention, deletion, and public retrieval are independent. An
approved metadata decision does not authorize downloading or retaining
content. Each approved operation requires one reviewed decision with:

- the affected operation and state;
- a reviewer authority class and basis code;
- decision and expiry timestamps;
- a review trigger; and
- the next safe action.

Robots denial, HTTP `401` or `403`, login or subscription requirements, rate
exhaustion, platform prohibition, unclear rights, conflicting evidence, and
expired evidence are durable blockers. Operators must not use cookies, old
tokens, alternate URLs, or alternate clients to bypass them.

The checked-in registry begins with every fact `unknown` and every operation
`pending`. Source inventories may update it only from bounded, sanitized,
reviewed evidence. Source content, response bodies, credentials, local paths,
personal details, and private proposal material do not belong in governance
records.

## Future project-native material

Project-native families have separate consent, retention, and deletion
contracts. They cover purpose, notice version, authority, confidentiality,
viewer roles, allowed uses, redaction, withdrawal, export, deletion ownership
and SLA, legal hold, retention expiry, derivative treatment, and minimal audit
events.

Use remains ineligible unless consent is active and unexpired, the requested
operation is explicitly allowed, retention is active and unexpired, and no
deletion trigger is pending. Restricted and sensitive operations also require
an allowed viewer role, and a redaction-required contract requires an explicit
redaction-complete evaluation context. Public retrieval is allowed only for
material classified public. Revocation or expiry removes all allowed uses and
creates a durable deletion request plus deletion or review and reindexing work.
A reviewed legal hold changes both content and derivative actions to review and
creates legal-hold review plus reindexing work; it does not restore use
eligibility.

The generic source evaluator rejects every project-native family. Those
families can be evaluated only through the combined consent, retention,
deletion, viewer-role, and exact-boolean redaction gate. Expiry transitions
cannot run before the approved expiry, and deletion request timestamps must
match their consent event or follow the retention expiry. The due time is
derived exactly from the declared deletion SLA.

Audit events contain only the schema version, consent ID, source ID, transition
type, and timestamp. They contain no names, contact details, roles in the
project, raw comments, private content, or proposal text.

No project-native ingestion may start until a later reviewed intake issue
supplies an approved notice, an accountable authority class, and a deletion
owner. Synthetic fixtures are content-free and do not represent real people.

The stricter contribution, inheritance, subject-export, exact deletion-work,
scoped legal-hold, and content-free tombstone rules are defined in
[`project-native-lifecycle.md`](project-native-lifecycle.md). Those portable
contracts do not imply that an intake UI or production access control has been
deployed.
