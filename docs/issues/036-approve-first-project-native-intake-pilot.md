# Approve the first project-native intake pilot

depends-on: 035
labels: rucksack-blocked

## Goal

Decide whether a future, bounded project-native intake pilot may collect one defined data class. Keep this issue blocked until the notice, consent, audience, retention, deletion owner, access control, and hosted-surface claims are reviewed; do not collect or fabricate material here.

## Acceptance tests

- Identify exactly one project-native data class and one purpose; do not combine artist submissions, visitor inputs, performer choices, generated scores, and visual-system history under a blanket approval.
- Provide a reviewed public-facing notice, explicit consent flow, allowed audiences and uses, retention period, withdrawal and export method, deletion SLA and owner role, derivative policy, incident contact role, and accessibility plan.
- Confirm the actual collection surface and access-control implementation. A loopback reference form is not described as hosted, production, authenticated, or publicly available.
- Confirm data minimization and prohibited fields, including no proposal or meeting material, personal duties, unnecessary names or contact details, private comments, credentials, or account identifiers.
- Confirm encrypted transit and storage, scoped secrets, secret scanning, sanitized logs, evidence policy, rate limits, abuse controls, and tested deletion propagation before any participant interaction.
- Define a maximum participant or record count, time window, byte limit, and stop rule. The pilot neither authorizes later data classes nor indefinite retention.
- A decline or incomplete response creates a durable blocker while public-source inventory, selection, search, and evaluation continue.

## Validation command

```bash
python3 -m unittest discover -s tests -v
scripts/agent-evidence
```

## Allowed secrets

None may be pasted or stored in this issue. Any later hosted pilot must declare secret names and deployment authority in a separate reviewed issue.

## Artifact outputs

- Reviewed approve or decline decision in this issue
- Public notice and synthetic consent-flow specification under `docs/` only if approved
- Durable blocker and exact missing authority when incomplete
- No participant record or private content

## Stop conditions

- Stop if purpose, data class, notice, consent, audience, retention, export, withdrawal, deletion, access control, hosting truth, or responsible role is incomplete.
- Stop if approval would permit multiple data classes, unbounded participation, indefinite retention, or unspecified derivatives.
- Stop if real private material, personal details, credentials, screenshots, account identifiers, endpoints, or local paths would enter the issue.
- Stop before any solicitation, collection, deployment, or participant contact.

## Human clarification protocol

Reply with approve or decline using privacy-safe role names and contract facts only. Do not paste participant data, personal names, contact information, credentials, account identifiers, private proposal text, or system endpoints.

## Recommended response

Decline until a real hosted or explicitly loopback-only surface, consent notice, least-privilege access, short retention, export and withdrawal path, deletion owner, and end-to-end synthetic deletion test all exist. When ready, approve one data class with a very small pilot limit.

## Trade-offs

Delaying real intake slows user-informed corpus development but prevents private material from entering an immature pipeline. A one-class pilot limits product learning while making consent and deletion observable.

## Free-form response

Decision:

Single data class and purpose:

Actual collection surface:

Consent and notice reference:

Allowed audiences and uses:

Retention period:

Withdrawal, export, and deletion contract:

Maximum records and pilot window:

Additional privacy-safe notes:
