# Source preservation and distinctness record

No contextual constraint is changed: `contextual_constraint_changes: []`.

- MIG-1 (`preserved=true`): timestamped HTTP reads and writes must overlap both active backfill and cutover intervals, span the complete migration without a gap over 1000 ms, and reconcile every acknowledged mutation against direct MongoDB state. The policy checker validates the evidence schema; the independent oracle validates real intervals, traffic continuity, links, and reconciliation.
- MIG-2 (`preserved=true`): paired source/candidate status and complete JSON bodies are recursively equal. Native lifecycle response fields and the empty 404 are fixed by focused source checks. Missing and blacklisted customers are mandatory source-captured pairs. A held-out real-time replay is checked against the acknowledged journal and direct target snapshots for stale, lost, duplicate, and impossible effects.
- MIG-3 (`preserved=true`): all external requests, including new deposit and withdrawal routes, are below 5000 ms. At least 20 source and post-cutover trace rows per method/route are referenced by partitions; the oracle independently recomputes nearest-rank p95 and enforces a maximum 10 ms delta.
- MIG-4 (`preserved=true`): raw container destroy/create events prove replacement with distinct IDs, mount evidence proves target-volume retention, network inspection proves PostgreSQL denial, effective configuration proves MongoDB-only selection, and a complete connection-observer interval plus attempt log proves zero source attempts while replacement-container probes read old/new accounts and perform a new write.

Independent challenge record (Michele, iteration 1)

- MIG-1: Michele found that phase labels did not prove overlap, uninterrupted traffic, or acknowledged-write reconciliation. The revised contract uses request and migration intervals, continuity bounds, linked journals, and direct snapshots.
- MIG-2: Michele found non-native lifecycle bodies and an oracle that evaluated baked observations instead of candidate evidence. The revised serialization contract matches native response DTOs/empty 404, exercises missing and blacklisted customers, and the oracle accepts a separately supplied evidence document.
- MIG-3: Michele found that only lifecycle routes were timed while financial calls could remain internal. The revised task requires public deposit/withdrawal routes and all four method/route latency partitions.
- MIG-4: Michele found a weakened process restart and trusted booleans. The revised schema requires distinct container replacement events, stable mount identity, PostgreSQL network inspection, effective profiles/bean output, complete connection-attempt logs, and replacement-container HTTP probes.

Family distinctness ledger

1. `account-lifecycle-http` primarily fails on exact lifecycle HTTP serialization or copied-document reconciliation.
2. `funds-linearizability` primarily fails on external financial-route invocation, real-time ordering, idempotency, or balance effects.
3. `durable-profile-cutover` primarily fails on container replacement, storage selection, volume retention, network isolation, or source connection attempts.

They reuse one evidence format but are not reworded or narrowed duplicates. Michele's post-repair independent re-review remains pending; this record does not claim that review occurred.

Author repair after Michele's second review

- MIG-1/MIG-2 evidence now binds every journal operation to the operation-specific public route and independently captured request account, amount, operation ID, and idempotency ID. Creation links its generated account through the native `201` response.
- Every acknowledged creation and every account visible through a successful candidate GET is now matched field-for-field to the final direct MongoDB snapshot, including the exact applied-operation ID set.
- Private regressions corrupt an HTTP-visible final document and swap applied deposit/withdraw request links; both must be rejected.
- Michele remains the designated independent challenger; post-repair re-review is pending and is not claimed here.
