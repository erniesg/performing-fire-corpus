# Trusted-VM ingestion worker

The trusted-VM worker is a portable, supervised state machine for one approved
asset at a time. The implementation in this repository is an offline reference
contract; it does not start a VM, configure a queue, read R2 secrets, or perform
network requests by itself.

## Durable boundaries

The worker receives two trusted interfaces:

- a durable control plane that atomically claims one job, owns leases and
  heartbeats, persists checkpoints, blockers, and terminal results, and returns
  no machine-local path or source bytes; and
- a current-authority resolver that rebuilds the job decision from rights,
  robots, access, MIME, byte, retention, storage-scope, selection, and policy
  records immediately before execution.

Portable callers must not substitute a VM-local SQLite file for the durable
control plane. A VM restart, disconnect, or lease expiry must leave the remote
checkpoint authoritative and make the job safely claimable again.

`schemas/v1/trusted-vm-worker.json` publishes strict v1 capability, job, lease,
heartbeat, checkpoint, result, and blocker contracts. Queue jobs carry stable
source, asset, locator, rights, selection, run-plan, evidence and policy IDs,
plus one exact immutable R2 key. They never carry a URL response body, media
bytes, signed URL, credential, header, cookie, device identifier, or local
path.

## Execution boundary

`run_trusted_vm_worker_once` claims at most one job. It requires concurrency
one, exact worker capabilities, a current lease, and all eight authority gates
before calling an acquisition executor. It checkpoints authority confirmation,
heartbeats the lease, accepts only a hash/size-bound exact-key receipt, then
checkpoints exact-key verification and persists the terminal result.

The production executor is responsible for the already reviewed bounded
transfer behavior:

- resolve the stable locator only after the current authority check;
- request at most one asset with final-URL, MIME, declared/observed byte,
  retry, rate and elapsed-time limits;
- use a disposable cache and remove it after success, failure, interruption,
  or lease loss;
- create only the reviewed immutable raw key and verify it by exact-key HEAD;
- persist object and provenance receipts before downstream enqueue; and
- recover a lost create response by exact-key HEAD without repeating a source
  request or overwriting an object.

Downstream handoff contains stable job IDs and exact object keys only. It never
contains the VM cache path.

## Blockers and resume

An absent, stale, mismatched, or denied authority produces a content-free
blocker with a stable outcome code, affected gate, authority class, exact safe
next action, and deterministic resume token. The blocked job does not hold any
unrelated queue node. An unexpected provider or executor exception is reduced
to `bounded_executor_failed`; provider bodies and exception text are not
persisted.

The generic Rucksack VM launcher is not part of this reference implementation.
Until its startup transport issue is resolved, this worker remains portable
and offline-tested and must not be represented as a running hosted service.
