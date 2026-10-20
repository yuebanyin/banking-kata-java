# Banking live-cutover evidence baseline

These fresh deterministic assets define evidence inputs and validation boundaries; they do not implement a migration. The heterogeneous cutover is from the repository's JPA/PostgreSQL storage to its Spring Data MongoDB storage.

A valid implementation must keep lifecycle and financial HTTP requests flowing across measured backfill and cutover intervals. It must provide paired source/candidate HTTP observations, an acknowledged-operation journal, and independently queried MongoDB snapshots. Supplying precomputed booleans is not evidence. Every request remains below 5000 ms, and nearest-rank p95 is recomputed separately for each method/route from at least 20 linked source and post-cutover trace rows. Deposit and withdrawal must therefore become public HTTP routes.

Lifecycle truth follows the native DTO/controller contract: successful open responses contain only `accountNumber`; successful views contain `accountNumber`, `fullName`, integer `balance`, and enum-string `score`; a missing-account GET has an empty JSON body represented as `null`. Missing-customer and blacklisted-customer requests are both mandatory paired observations. Because their current exceptions are propagated outside the controller, the harness captures the complete source HTTP status/body rather than inventing a response DTO, then requires recursive equality after routing changes.

Permanent cutover means actual application-container replacement, not a process restart. The evidence includes destroy/create events with distinct container IDs, stable MongoDB volume mounts, PostgreSQL network-denial inspection, effective profiles and selected storage bean, a connection observer interval and attempt log, and old/new reads plus a write in the replacement container.

The three contract families are materially distinct: lifecycle serialization/reconciliation, financial linearizability/exactly-once effects, and durable backend/configuration/deployment selection. They share the same strict traffic and latency envelope.

## Evidence linkage and final reconciliation

Candidate evidence uses schema version 5. Every HTTP trace records nullable captured request account, amount, operation ID, and idempotency key fields. Transport errors are forbidden for every trace, and every expected HTTP error must have exact source/candidate status and body parity. Each acknowledged journal row must link to a candidate POST on the route for its operation kind. Financial rows must match all four captured request values, the route-specific status/body contract, and an exact source request pair; account creation must match its IDs and the generated `201` response account number and is unconditionally `applied`.

The final snapshot must be a direct MongoDB query. It must contain every acknowledged creation and every account returned by a successful candidate GET. For each such account, `accountNumber`, `fullName`, `balance`, and `score` must equal its latest view before that snapshot, while `appliedOperationIds` must exactly equal the applied journal `operation_id` values (not candidate classifications or idempotency keys). Every acknowledged `201` creation must contribute its exact operation ID.

## Schema v5 bidirectional evidence accounting


The independent verifier closes every evidence link in both directions:

- Every non-null `comparison_id` must identify exactly one source trace and one candidate trace. Their method, route, request account/amount, complete status, and complete body must agree. This applies to every captured comparison, not only the named mandatory lifecycle cases.
- Every successful candidate `POST` must carry operation and idempotency IDs and map to exactly one semantically matching journal entry. Every operation-bearing candidate `POST` (including expected domain failures) must also map exactly once; journals may not share request traces.
- Every direct MongoDB `appliedOperationIds` value must name an independently replayed, applied journal operation for that same account. The final list must be complete and ordered, with no orphan or duplicate snapshot operations.
- For every held-out financial account, the final direct MongoDB document must exactly match independent initial truth plus replay: account number, full name, score, final balance, and complete applied-operation ID list.

Duplicate/orphan trace IDs, comparison sides, journal links, account documents, and snapshot operation IDs are invalid evidence rather than ignorable telemetry.
