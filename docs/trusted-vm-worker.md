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
heartbeats the lease using a fresh clock reading, accepts only a
hash/size-bound exact-key receipt, then checkpoints exact-key verification and
persists the terminal result. A process interrupt releases the lease; a
provider or authority blocker holds only that job.

`BoundedTrustedVMAcquisitionExecutor` is the portable adapter to the reviewed
full-corpus object contract. Its context resolver receives stable job IDs and
returns the public locator only in trusted process memory. The context also
binds the rights snapshot, retention class, run, evidence, downstream job IDs,
one elapsed budget, and a source-request budget fixed at one. It:

- checks for a matching exact object before any source request, enabling
  restart recovery without reacquisition;
- obtains one explicit rate permit and requests at most one asset;
- enforces exact final URL, HTTP status, MIME, declared and observed bytes,
  content hash, elapsed time, and the reviewed raw target key;
- uses a disposable cache and removes its partial file after success, failure,
  interruption, or lease loss;
- calls the immutable conditional-create contract, which handles a lost create
  response only through exact-key `HEAD`;
- commits the content-bound object receipt to durable receipt authority; and
- returns a deterministic provenance receipt ID with downstream job IDs.

The control plane persists that terminal result as the acquisition provenance
receipt before making its downstream IDs runnable. A crash before terminal
completion is safe to reclaim: the adapter verifies and receipts the exact
pre-existing object without another source request. It never overwrites, lists,
or deletes an object.

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

## Production wiring

The trusted-VM composition root must inject:

- the current stable-ID context resolver;
- the reviewed no-redirect streaming HTTP client with a socket timeout;
- the scoped R2 exact-key client;
- durable receipt authority backed by the corpus ledger;
- an ignored disposable cache directory;
- an aware monotonic wall-clock adapter; and
- a source-scoped rate-permit adapter.

The composition root may read the four reviewed R2 secret names. No secret
value, provider response, account identifier, signed URL, cookie, header,
source body, or machine path may enter the queue, checkpoint, result, blocker,
metric, or evidence record.
