# Provenance-aware, rights-filtered search index

The search index joins the complete known metadata universe with the
deliberately selected rich corpus without treating them as the same thing.
It is a portable contract and synthetic test surface, not a deployed search service and not authority to retrieve source content.

## Field-level boundary

An `index-document` contains only concise reviewed metadata. Every field binds
to one `provenance-edge` and one current `visibility-policy`. Together they
identify:

- the stable source, asset, origin record, evidence time, and evidence expiry;
- whether the value is factual source metadata, a derived observation, a
  generated score, or a project-native record;
- the rights and consent snapshots, retention class, visibility class,
  permitted operations and audiences, and review trigger;
- the selection state and any provenance-preserving duplicate cluster.

Raw objects, media, full source prose, signed URLs, credentials, private
proposal material, personal identifiers, and machine-local paths are forbidden
from index fields. Any alphabetic scheme-like `token:` sequence is forbidden,
including empty, encoded, custom-protocol, or whitespace-obfuscated forms.
Percent-encoded octets, numeric HTML entities, and Unicode colon lookalikes
are rejected rather than decoded inside the index boundary.
Field names are stored separately, so renderers can add label punctuation
without persisting an ambiguous locator-like value.

## Query-time authority

The snapshot is immutable and content-bound, but an old snapshot is never
current visibility authority. Every query resolves the current policy for each
exact document field through a trusted authority boundary. It also resolves
the current compiled document and exact provenance edge, whose authoritative
digest binds the field name and value. A retained replacement also requires
the exact current deletion/reindex event. Missing authority, resolver failure,
source correction, missing or changed event authority, pending or revoked
rights, expired evidence, expired policy, withdrawn consent, deletion-due
retention, an ungranted operation, or an ungranted audience excludes that
field. Unknown never defaults visible.

Queries can additionally constrain source, language, period, medium, selection
state, and duplicate cluster. Results expose the exact visible field IDs,
values, origin classes, provenance edges, and current policy identifiers so a
caller can render provenance without exposing hidden fields.

## Duplicates and exact deletion

A `duplicate-cluster` is evidence, not canonical-source replacement. Every
member retains its distinct source, asset, index document, and provenance
edges. Snapshot validation rejects a cluster that collapses identities or
references a record that the snapshot did not preserve.

A `deletion-event` targets one exact document field for rights revocation,
consent withdrawal, source correction, retention expiry, or transformation
replacement. Snapshot construction resolves the current event through the
trusted authority boundary and requires the exact field to exist. An
exact-field replacement retains only the current resolver-verified document,
field, provenance edge, and policy. It must differ from the explicit
pre-event provenance record, so a no-op cannot satisfy a replacement event.
It cannot be inferred or broadened to a document, source, prefix, or
object-store deletion.

Provenance edges list their complete transformation inputs. Removing or
replacing an input requires an exact current event for every transitive
derived or generated dependent; otherwise snapshot construction stops.
When events exist, the snapshot also binds the complete pre-event provenance
lineage so a later validator can reproduce the exact affected frontier and
verify that the final edge set matches every recorded action. Unaffected
pre-event edges must be byte-for-byte equal in the final set; replacements
must differ and removals must be absent. Every event carries the canonical hash
of that complete lineage as its authority snapshot. The lineage evidence must
already exist—and remain unexpired—at every event time, so neither a
contradictory graph nor post-event evidence can be substituted later.

An exact-field removal deletes the matching field, provenance edge, and
visibility policy while leaving unrelated fields intact. Affected duplicate
evidence must be rebuilt rather than silently retaining a deleted provenance
edge. Snapshot construction resolves the emitted post-removal document and
all retained edges and policies as the current authority, so the same
authority boundary remains usable at query time.

Project-native or private fields require approved consent, non-public
audiences, and the `project_native_expiring` retention class. Standalone and
nested schemas reject a generic inventory or selected-corpus retention class
for those fields.

## Versioned contracts

The v1 records are `index-document`, `provenance-edge`,
`duplicate-cluster`, `visibility-policy`, `deletion-event`, and
`index-snapshot`. Schemas are strict and runtime validation recomputes
cross-record identity, snapshot hashes, ordering, authority hashes, evidence
windows, deletion targets, and duplicate membership.

This issue uses sanitized synthetic fixtures only. It performs no source
request, R2 access, private-data ingestion, production indexing, or deletion
of files or corpus objects.
