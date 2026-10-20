# Banking live-cutover fixture baseline

These deterministic fixtures describe inputs and evidence boundaries; they do not implement a migration. The source is the repository's JPA/PostgreSQL account storage and the heterogeneous target is its MongoDB account storage.

All future contracts preserve four rules together: traffic overlaps backfill and cutover; status and complete JSON bodies match controlled source behavior with no stale/lost/duplicate effects; every request is below 5000 ms and each method-and-route p95 is at most 10 ms over its own baseline; and a fresh application process works from persisted target state while PostgreSQL is network-denied.

The fixtures intentionally expose three non-duplicate contract families:

- `account-lifecycle-http` is about account creation/lookup parity and copy reconciliation.
- `funds-linearizability` is about same-account financial contention and exactly-once effects; public GET observations and latency probes remain required.
- `durable-profile-cutover` is about effective adapter selection, source isolation, and deployment permanence; HTTP parity and latency probes remain required but are not its primary oracle.

Expected domain errors are valid only when their status and entire body equal the controlled source baseline. Transport errors, timeouts, unexpected 5xx responses, stale reads, and acknowledged writes absent from MongoDB are failures. Target persistence is retained across the fresh application restart; the target volume is not reset.

`generate_policy_assets.py` is byte-deterministic for seed 41031. `check_policy_assets.py` validates the fixture relationships and confirms that named native candidate seams still exist. No file in this directory is a migration implementation.
