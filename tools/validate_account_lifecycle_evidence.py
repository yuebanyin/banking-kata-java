#!/usr/bin/env python3
"""Public schema and semantic checks for account cutover evidence."""
import argparse
import json
import math
import uuid
from pathlib import Path

ROUTES = {
    ("POST", "/bank-accounts"),
    ("GET", "/bank-accounts/{accountNumber}"),
    ("POST", "/bank-accounts/{accountNumber}/deposits"),
    ("POST", "/bank-accounts/{accountNumber}/withdrawals"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_schema(evidence, schema_path):
    schema = json.loads(Path(schema_path).read_text())
    try:
        import jsonschema
        jsonschema.Draft202012Validator(schema).validate(evidence)
    except ImportError:
        require(evidence.get("schema_version") == 5, "schema_version must be 5")
        for key in schema["required"]:
            require(key in evidence, "missing required field " + key)


def nearest_p95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * .95) - 1)]


def _leaf_strings(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _leaf_strings(child)
    elif isinstance(value, str):
        yield value


def _declared_values(workload, field_names):
    values = set()
    def visit(value, parent=None):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in field_names:
                    values.update(_leaf_strings(child))
                visit(child, key)
        elif isinstance(value, list):
            for child in value:
                visit(child, parent)
    visit(workload)
    return values


def _semantic_account_numbers(evidence):
    """Account identifiers observed in account-specific evidence fields only."""
    values = set()
    for trace in evidence.get("http_trace", []):
        account = trace.get("request_account_number")
        if isinstance(account, str):
            values.add(account)
        body = trace.get("body")
        if isinstance(body, dict) and isinstance(body.get("accountNumber"), str):
            values.add(body["accountNumber"])
    for row in evidence.get("operation_journal", []):
        if isinstance(row.get("account_number"), str):
            values.add(row["account_number"])
    for snapshot in evidence.get("mongo_snapshots", []):
        for account in snapshot.get("accounts", []):
            if isinstance(account.get("accountNumber"), str):
                values.add(account["accountNumber"])
    return values


def _workload_operation_records(workload):
    """Yield workload records which describe externally observable mutations."""
    records = []
    operation_arrays = set()
    idempotency_arrays = set()

    def visit(value):
        if isinstance(value, dict):
            kind = value.get("kind")
            operation_id = value.get("operation_id")
            if isinstance(operation_id, str) and kind in ("create", "deposit", "withdraw"):
                records.append(value)
            for key, child in value.items():
                if key == "operation_ids":
                    operation_arrays.update(_leaf_strings(child))
                elif key == "idempotency_keys":
                    idempotency_arrays.update(_leaf_strings(child))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(workload)
    return records, operation_arrays, idempotency_arrays



def _financial_alias_accounts(workload):
    """Reproduce the public runner's value-based alias allocation."""
    financial = next((item for item in workload.get("contract_families", [])
                      if item.get("family_id") == "funds-linearizability"), {})
    aliases = financial.get("initial_balances", {})
    require(isinstance(aliases, dict), "financial initial_balances must be an object")
    numbers = workload.get("controlled_collaborators", {}).get("account_numbers", [])
    require(isinstance(numbers, list), "controlled account_numbers must be an array")
    seed = int(workload.get("fixture_seed", 0))
    result = {}
    for index, alias in enumerate(aliases):
        result[alias] = (str(numbers[index]) if index < len(numbers) else
                         str(uuid.uuid5(uuid.NAMESPACE_URL, "%s:%s:%s" % (seed, index, alias))))
    return result

def validate_workload_binding(evidence, workload):
    """Bind workload declarations to their corresponding semantic evidence fields.

    Provenance, run IDs and other arbitrary strings deliberately do not count.  A
    workload account must occur in an account field, and each declared mutation
    must occur as the matching journal row and HTTP request.
    """
    semantic_accounts = _semantic_account_numbers(evidence)
    accounts = _declared_values(workload, {"account_number", "account_numbers"})
    records, operation_arrays, idempotency_arrays = _workload_operation_records(workload)
    alias_accounts = _financial_alias_accounts(workload)
    require(accounts or records or operation_arrays or idempotency_arrays,
            "workload declares no bindable account or operation identifiers")
    missing_accounts = sorted(accounts - semantic_accounts)
    require(not missing_accounts,
            "evidence does not match workload account identifiers: " +
            ", ".join(missing_accounts[:5]))

    journals = evidence.get("operation_journal", [])
    journal_by_operation = {row.get("operation_id"): row for row in journals}
    journal_operations = {value for value in journal_by_operation if isinstance(value, str)}
    journal_idempotency = {row.get("idempotency_key") for row in journals
                           if isinstance(row.get("idempotency_key"), str)}
    traces = evidence.get("http_trace", [])

    missing_operations = sorted(operation_arrays - journal_operations)
    require(not missing_operations,
            "evidence does not match workload operation identifiers: " +
            ", ".join(missing_operations[:5]))
    missing_keys = sorted(idempotency_arrays - journal_idempotency)
    require(not missing_keys,
            "evidence does not match workload idempotency identifiers: " +
            ", ".join(missing_keys[:5]))

    for declaration in records:
        operation_id = declaration["operation_id"]
        require(operation_id in journal_by_operation,
                "evidence does not match workload operation identifier: " + operation_id)
        row = journal_by_operation[operation_id]
        if isinstance(declaration.get("amount"), int):
            require(row.get("amount") == declaration["amount"],
                    "workload amount mismatch for " + operation_id)
        expected_account = None
        if isinstance(declaration.get("account_number"), str):
            expected_account = declaration["account_number"]
        elif isinstance(declaration.get("account"), str):
            alias = declaration["account"]
            require(alias in alias_accounts,
                    "workload operation references unknown account alias: " + alias)
            expected_account = alias_accounts[alias]
        if expected_account is not None:
            require(row.get("account_number") == expected_account,
                    "workload account mismatch for " + operation_id)
            require(expected_account in semantic_accounts,
                    "workload account alias is absent from HTTP/journal/snapshot evidence: " +
                    declaration.get("account", expected_account))
        require(row.get("kind") == declaration["kind"],
                "workload operation kind mismatch for " + operation_id)
        if isinstance(declaration.get("idempotency_key"), str):
            require(row.get("idempotency_key") == declaration["idempotency_key"],
                    "workload idempotency key mismatch for " + operation_id)

        matching_traces = [trace for trace in traces
                           if trace.get("origin") == "candidate" and
                           trace.get("request_operation_id") == operation_id]
        require(len(matching_traces) == 1,
                "workload operation must map to exactly one candidate HTTP trace: " + operation_id)
        trace = matching_traces[0]
        if expected_account is not None:
            require(trace.get("request_account_number") == expected_account,
                    "workload HTTP account mismatch for " + operation_id)
        if isinstance(declaration.get("amount"), int):
            require(trace.get("request_amount") == declaration["amount"],
                    "workload HTTP amount mismatch for " + operation_id)
        if isinstance(declaration.get("idempotency_key"), str):
            require(trace.get("request_idempotency_key") == declaration["idempotency_key"],
                    "workload HTTP idempotency key mismatch for " + operation_id)


def validate_observation_provenance(evidence):
    """Reject same-path pseudo-pairs and audit windows which miss replacement traffic."""
    traces = evidence.get("http_trace", [])
    pairs = {}
    for trace in traces:
        comparison = trace.get("comparison_id")
        if comparison is not None:
            pairs.setdefault(comparison, []).append(trace)
    for comparison, rows in pairs.items():
        source = [row for row in rows if row.get("origin") == "source"]
        candidate = [row for row in rows if row.get("origin") == "candidate"]
        require(len(source) == 1 and len(candidate) == 1,
                "comparison must have exactly one source receipt and candidate request: " + comparison)
        # The runner executes the candidate exactly once, then observes its committed
        # source receipt through the direct JPA endpoint.  Concurrent/same-call labels
        # cannot satisfy this ordering.
        require(source[0]["started_ms"] >= candidate[0]["completed_ms"],
                "source receipt predates candidate completion: " + comparison)
        require(source[0]["trace_id"] != candidate[0]["trace_id"],
                "source and candidate traces are not independent: " + comparison)

    deployment = evidence.get("deployment_evidence", {})
    events = {row.get("event"): row for row in deployment.get("container_events", [])}
    require("destroy" in events and "create" in events,
            "replacement requires Docker destroy and create events")
    destroy = events["destroy"]
    create = events["create"]
    require(destroy.get("container_id") == deployment.get("old_container_id"),
            "destroy event is not bound to old container")
    require(create.get("container_id") == deployment.get("new_container_id"),
            "create event is not bound to replacement container")
    require(destroy.get("at_ms", 0) <= create.get("at_ms", -1),
            "Docker replacement event chronology is invalid")

    observer = deployment.get("connection_observer", {})
    require(observer.get("continuous") is True,
            "source-attempt observer is not continuous")
    require(observer.get("socket_sample_count", 0) > 0 and
            observer.get("postgresql_session_sample_count", 0) > 0,
            "continuous observer has no socket/session samples")
    require(observer.get("application_container_id") == deployment.get("new_container_id"),
            "observer is not bound to replacement container")
    require(observer.get("started_ms", 2**63 - 1) <= create.get("at_ms", -1),
            "source audit started after replacement creation")

    by_id = {trace.get("trace_id"): trace for trace in traces}
    restart_ids = deployment.get("restart_trace_ids", [])
    require(bool(restart_ids), "replacement has no restart probes")
    restart = []
    for trace_id in restart_ids:
        require(trace_id in by_id, "replacement probe trace is missing: " + str(trace_id))
        trace = by_id[trace_id]
        require(trace.get("container_id") == deployment.get("new_container_id"),
                "replacement probe belongs to another container: " + str(trace_id))
        require(trace.get("started_ms", -1) >= create.get("at_ms", 2**63 - 1),
                "replacement probe predates Docker create event: " + str(trace_id))
        restart.append(trace)
    last_probe_ms = max(trace.get("completed_ms", 2**63 - 1) for trace in restart)
    require(observer.get("completed_ms", -1) >= last_probe_ms,
            "source audit stopped before final replacement probe")
    for attempt in deployment.get("connection_attempt_log", []):
        require(observer["started_ms"] <= attempt.get("at_ms", -1) <= observer["completed_ms"],
                "source attempt lies outside observer window")

def validate(evidence):
    validate_observation_provenance(evidence)
    require(evidence["schema_version"] == 5, "schema version mismatch")
    traces = evidence["http_trace"]
    by_id = {}
    comparisons = {}
    for trace in traces:
        trace_id = trace["trace_id"]
        require(trace_id not in by_id, "duplicate trace_id: " + trace_id)
        by_id[trace_id] = trace
        require(trace["completed_ms"] >= trace["started_ms"], "negative trace duration")
        require(trace["completed_ms"] - trace["started_ms"] < 5000, "request exceeded 5000ms")
        require(trace["transport_error"] is None, "transport error in " + trace_id)
        require(trace["status"] < 500, "unexpected server failure in " + trace_id)
        if trace["comparison_id"] is not None:
            comparisons.setdefault(trace["comparison_id"], []).append(trace)
    for comparison_id, pair in comparisons.items():
        require(len(pair) == 2, "comparison must contain exactly two traces: " + comparison_id)
        require({p["origin"] for p in pair} == {"source", "candidate"}, "comparison origins invalid")
        require(pair[0]["status"] == pair[1]["status"] and pair[0]["body"] == pair[1]["body"],
                "response mismatch: " + comparison_id)
        request_fields = ("method", "route_template", "request_account_number", "request_amount",
                          "request_operation_id", "request_idempotency_key")
        require(all(pair[0][field] == pair[1][field] for field in request_fields),
                "paired requests differ: " + comparison_id)

    journals = evidence["operation_journal"]
    journal_by_operation = {}
    journal_by_trace = {}
    last_index = -1
    for row in journals:
        require(row["operation_id"] not in journal_by_operation, "duplicate journal operation")
        require(row["request_trace_id"] not in journal_by_trace, "journal trace linked twice")
        require(row["linearization_index"] > last_index, "journal is not in linearization order")
        last_index = row["linearization_index"]
        journal_by_operation[row["operation_id"]] = row
        journal_by_trace[row["request_trace_id"]] = row
        require(row["request_trace_id"] in by_id, "orphan journal trace")
        request = by_id[row["request_trace_id"]]
        require(request["method"] == "POST", "journal links a non-POST")
        require(request["request_operation_id"] == row["operation_id"], "operation link mismatch")
        require(request["request_idempotency_key"] == row["idempotency_key"], "idempotency mismatch")
        require(request["request_account_number"] == (row["account_number"] or None)
                or row["kind"] == "create", "account link mismatch")
        require(request["request_amount"] == row["amount"] or row["kind"] == "create", "amount mismatch")
        expected_route = {"create": "/bank-accounts", "deposit": "/bank-accounts/{accountNumber}/deposits",
                          "withdraw": "/bank-accounts/{accountNumber}/withdrawals"}[row["kind"]]
        require(request["route_template"] == expected_route, "journal route mismatch")
        expected_status = {"applied": 201 if row["kind"] == "create" else 204,
                           "duplicate_ignored": 201 if row["kind"] == "create" else 204,
                           "invalid_amount": 400, "account_not_found": 404,
                           "insufficient_funds": 409}.get(row["result"])
        if expected_status is not None:
            require(request["status"] == expected_status, "journal result/status mismatch")
            require(request["body"] is None or row["kind"] == "create",
                    "mutation response body must be empty")
        if row["kind"] == "create" and row["result"] in ("applied", "duplicate_ignored"):
            require(request["body"] == {"accountNumber": row["account_number"]},
                    "creation journal/response mismatch")
    for trace in traces:
        if trace["origin"] == "candidate" and trace["method"] == "POST" and (
                trace["status"] < 300 or trace["request_operation_id"] is not None):
            require(trace["trace_id"] in journal_by_trace, "candidate POST has no journal: " + trace["trace_id"])

    kinds = set()
    for interval in evidence["migration_intervals"]:
        kinds.add(interval["kind"])
        lo, hi = interval["started_ms"], interval["completed_ms"]
        reads = [t for t in traces if t["method"] == "GET" and t["status"] == 200
                 and t["started_ms"] <= hi and t["completed_ms"] >= lo]
        writes = [r for r in journals if r["result"] in ("applied", "duplicate_ignored")
                  and r["acknowledged"] and r["request_trace_id"] in by_id
                  and by_id[r["request_trace_id"]]["status"] < 300
                  and r["started_ms"] <= hi and r["completed_ms"] >= lo]
        require(reads, "interval has no successful GET: " + interval["kind"])
        require(writes, "interval has no acknowledged successful write: " + interval["kind"])
    require(kinds == {"backfill", "cutover"}, "backfill/cutover intervals required")

    final = [s for s in evidence["mongo_snapshots"] if s["purpose"] == "final_reconciliation"]
    require(len(final) == 1, "one final MongoDB snapshot required")
    accounts = final[0]["accounts"]
    account_by_number = {a["accountNumber"]: a for a in accounts}
    require(len(account_by_number) == len(accounts), "duplicate MongoDB account")
    known_applied = {r["operation_id"] for r in journals if r["result"] == "applied" and r["acknowledged"]}
    snapshot_ops = []
    for account in accounts:
        ids = account["appliedOperationIds"]
        require(len(ids) == len(set(ids)), "duplicate applied operation on account")
        snapshot_ops.extend(ids)
        require(set(ids) <= known_applied, "orphan operation in MongoDB snapshot")
        expected_ids = [row["operation_id"] for row in journals
                        if row["account_number"] == account["accountNumber"]
                        and row["result"] == "applied" and row["acknowledged"]]
        require(ids == expected_ids, "MongoDB operations are missing, orphaned, or out of order")
    for row in journals:
        if row["result"] == "applied" and row["acknowledged"]:
            require(row["account_number"] in account_by_number, "applied operation account missing")
            require(row["operation_id"] in account_by_number[row["account_number"]]["appliedOperationIds"],
                    "applied operation absent from MongoDB")

    require(len(evidence["latency_partitions"]) == 4, "exactly four latency partitions required")
    require({(p["method"], p["route_template"]) for p in evidence["latency_partitions"]} == ROUTES,
            "latency route partitions incomplete")
    for part in evidence["latency_partitions"]:
        baseline, post = part["baseline_trace_ids"], part["post_cutover_trace_ids"]
        require(len(baseline) >= 20 and len(post) >= 20, "latency partition needs 20 samples per period")
        samples = []
        for ids in (baseline, post):
            current = []
            for trace_id in ids:
                require(trace_id in by_id, "orphan latency trace")
                trace = by_id[trace_id]
                require((trace["method"], trace["route_template"]) ==
                        (part["method"], part["route_template"]), "latency route mismatch")
                current.append(trace["completed_ms"] - trace["started_ms"])
            samples.append(current)
        require(nearest_p95(samples[1]) <= nearest_p95(samples[0]) + 10, "p95 regression exceeds 10ms")

    deployment = evidence["deployment_evidence"]
    require(deployment["old_container_id"] != deployment["new_container_id"], "container was not replaced")
    events = deployment["container_events"]
    require(any(e["event"] == "destroy" and e["container_id"] == deployment["old_container_id"] for e in events),
            "old container destroy event absent")
    require(any(e["event"] == "create" and e["container_id"] == deployment["new_container_id"] for e in events),
            "new container create event absent")
    require(deployment["target_mount_before"] == deployment["target_mount_after"], "MongoDB volume changed")
    require(all(c["engine"] != "postgresql" or c["outcome"] == "denied"
                for c in deployment["connection_attempt_log"]), "PostgreSQL connection after cutover")
    network = deployment["postgresql_network_inspect"]
    require(network.get("network_id") and network.get("source_network_denied") is True,
            "PostgreSQL network denial was not directly observed")
    require(network.get("application_container_id") == deployment["new_container_id"],
            "network observation is not scoped to replacement container")
    require(network.get("postgresql_container_id") and
            network.get("postgresql_container_id") not in network.get("attached_container_ids", []),
            "source container remains on replacement network")
    require(deployment["new_container_id"] in network.get("attached_container_ids", []),
            "replacement container absent from observed network")
    config = deployment["effective_configuration"]
    active_profiles = config.get("active_profiles", [])
    require(config.get("storage") == "mongodb" and not config.get("postgresql_enabled", True) and
            "target-only" in active_profiles and "adapter-persistence-jpa" not in active_profiles and
            "mongo" in config.get("selected_storage_bean", "").lower(),
            "replacement is not observed MongoDB-only")
    observer = deployment["connection_observer"]
    require(observer.get("complete") is True and
            observer.get("application_container_id") == deployment["new_container_id"] and
            observer.get("source_container_id") == network.get("postgresql_container_id"),
            "connection observer is incomplete or unscoped")
    require(observer.get("postgres_established_socket_count") == 0 and
            observer.get("replacement_postgresql_session_count") == 0 and
            observer.get("postgres_log_line_count") == 0,
            "PostgreSQL connection was observed after cutover")
    require(network.get("network_id") in observer.get("observed_network_ids", []) and
            network.get("network_id") not in observer.get("source_network_ids", []) and
            not (set(observer.get("observed_network_ids", [])) &
                 set(observer.get("source_network_ids", []))),
            "replacement and PostgreSQL still share an observed network")
    require(all(isinstance(observer.get(key), str) and observer.get(key)
                for key in ("proc_net_sha256", "pg_stat_activity_sha256", "application_log_sha256")),
            "connection observations lack captured-content digests")
    restart = [by_id[x] for x in deployment["restart_trace_ids"]]
    require(all(t["container_id"] == deployment["new_container_id"] and t["status"] < 300 for t in restart),
            "failed replacement-container probe")
    get_probes = [trace for trace in restart if trace["method"] == "GET"]
    post_probes = [trace for trace in restart if trace["method"] == "POST"]
    require(len(get_probes) >= 3 and post_probes, "replacement probes incomplete")
    journalled_posts = [trace for trace in post_probes if trace["trace_id"] in journal_by_trace
                         and journal_by_trace[trace["trace_id"]]["acknowledged"]
                         and journal_by_trace[trace["trace_id"]]["result"]
                         in ("applied", "duplicate_ignored")]
    require(journalled_posts, "replacement write is not successfully journalled")
    require(any(read["request_account_number"] == post["request_account_number"]
                and read["started_ms"] >= post["completed_ms"]
                for post in journalled_posts for read in get_probes),
            "replacement write has no read-after-write")
    require(len({trace["request_account_number"] for trace in get_probes}) >= 2,
            "replacement probes do not cover old and new accounts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--schema", default="tb4_artifacts/candidate_evidence.schema.json")
    args = parser.parse_args()
    workload = json.loads(Path(args.workload).read_text())
    evidence = json.loads(Path(args.evidence).read_text())
    validate_schema(evidence, args.schema)
    validate(evidence)
    validate_workload_binding(evidence, workload)
    print("account lifecycle evidence valid")


if __name__ == "__main__":
    main()
