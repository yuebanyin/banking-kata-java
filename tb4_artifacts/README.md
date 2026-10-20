# Banking live-cutover evidence baseline

These fresh deterministic assets define evidence inputs and validation boundaries; they do not implement a migration. The heterogeneous cutover is from the repository's JPA/PostgreSQL storage to its Spring Data MongoDB storage.

A valid implementation must keep lifecycle and financial HTTP requests flowing across measured backfill and cutover intervals. It must provide paired source/candidate HTTP observations, an acknowledged-operation journal, and independently queried MongoDB snapshots. Supplying precomputed booleans is not evidence. Every request remains below 5000 ms, and nearest-rank p95 is recomputed separately for each method/route from at least 20 linked source and post-cutover trace rows. Deposit and withdrawal must therefore become public HTTP routes.

Lifecycle truth follows the native DTO/controller contract: successful open responses contain only `accountNumber`; successful views contain `accountNumber`, `fullName`, integer `balance`, and enum-string `score`; a missing-account GET has an empty JSON body represented as `null`. Missing-customer and blacklisted-customer requests are both mandatory paired observations. Because their current exceptions are propagated outside the controller, the harness captures the complete source HTTP status/body rather than inventing a response DTO, then requires recursive equality after routing changes.

Permanent cutover means actual application-container replacement, not a process restart. The evidence includes destroy/create events with distinct container IDs, stable MongoDB volume mounts, PostgreSQL network-denial inspection, effective profiles and selected storage bean, a connection observer interval and attempt log, and old/new reads plus a write in the replacement container.

The three contract families are materially distinct: lifecycle serialization/reconciliation, financial linearizability/exactly-once effects, and durable backend/configuration/deployment selection. They share the same strict traffic and latency envelope.
