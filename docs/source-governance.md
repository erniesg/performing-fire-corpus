# Source governance and project-native lifecycle

The canonical source registry identifies the known research universe. The
source-governance registry separately records whether a particular operation is
currently eligible. Registration never grants permission.

## Fail-closed source decisions

Each source has explicit facts for access control, API availability,
authentication, copyright or lawful basis, platform terms, and robots. A
non-expired observation must support every fact. Unknown, stale, conflicting,
missing, future-dated, or expired evidence blocks the operation.

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
deletion trigger is pending. Revocation or expiry removes all allowed uses and
creates content deletion, derivative deletion, and reindexing work. A reviewed
legal hold prevents silent deletion and instead creates legal-hold review plus
reindexing work; it does not restore use eligibility.

Audit events contain only the schema version, consent ID, source ID, transition
type, and timestamp. They contain no names, contact details, roles in the
project, raw comments, private content, or proposal text.

No project-native ingestion may start until a later reviewed intake issue
supplies an approved notice, an accountable authority class, and a deletion
owner. Synthetic fixtures are content-free and do not represent real people.
