#!/usr/bin/env python3
"""Public schema and semantic checks for account cutover evidence."""
import argparse
import json
import math
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


def validate_workload_binding(evidence, workload):
    """Reject valid-but-stale evidence produced for a different workload.

    Public-format workloads declare controlled account and operation identifiers.  Those
    identifiers are opaque: semantic validation binds them to observed leaves instead of
    depending on the public fixture's labels or values.
    """
    observed = set(_leaf_strings(evidence))
    accounts = _declared_values(workload, {"account_number", "account_numbers"})
    operations = _declared_values(workload, {
        "operation_id", "operation_ids", "idempotency_key", "idempotency_keys"})
    required = accounts | operations
    require(required, "workload declares no bindable account or operation identifiers")
    missing = sorted(value for value in required if value not in observed)
    require(not missing, "evidence does not match workload identifiers: " + ", ".join(missing[:5]))

    # Bind declared financial payloads, when present, to their durable journal row.
    journal = {row["operation_id"]: row for row in evidence["operation_journal"]}
    def bind_payloads(value):
        if isinstance(value, dict):
            operation_id = value.get("operation_id")
            if operation_id in journal:
                row = journal[operation_id]
                if isinstance(value.get("amount"), int):
                    require(row["amount"] == value["amount"],
                            "workload amount mismatch for " + operation_id)
                if isinstance(value.get("account_number"), str):
                    require(row["account_number"] == value["account_number"],
                            "workload account mismatch for " + operation_id)
                if isinstance(value.get("kind"), str) and value["kind"] in ("create", "deposit", "withdraw"):
                    require(row["kind"] == value["kind"],
                            "workload operation kind mismatch for " + operation_id)
            for child in value.values():
                bind_payloads(child)
        elif isinstance(value, list):
            for child in value:
                bind_payloads(child)
    bind_payloads(workload)


def validate(evidence):
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
    config = deployment["effective_configuration"]
    require(config.get("storage") == "mongodb" and not config.get("postgresql_enabled", True),
            "replacement is not MongoDB-only")
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
