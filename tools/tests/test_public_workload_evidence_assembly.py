import importlib.util
import json
import tempfile
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "runner_public_assembly", ROOT / "tools/account_lifecycle_cutover.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def schema_specimen(node, root, salt="fixture"):
    """Build observation-boundary fixtures that satisfy the checked-in schema."""
    if "$ref" in node:
        target = root
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_specimen(target, root, salt)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    for choice in ("oneOf", "anyOf"):
        if choice in node:
            return schema_specimen(node[choice][0], root, salt)
    kind = node.get("type")
    if isinstance(kind, list):
        kind = next((value for value in kind if value != "null"), "null")
    if kind == "object":
        return {key: schema_specimen(node.get("properties", {})[key], root,
                                     salt + "-" + key)
                for key in node.get("required", [])}
    if kind == "array":
        count = node.get("minItems", 0)
        return [schema_specimen(node.get("items", {}), root, "%s-%d" % (salt, index))
                for index in range(count)]
    if kind == "integer":
        return max(0, node.get("minimum", 0))
    if kind == "number":
        return max(0, node.get("minimum", 0))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    if kind == "string":
        value = salt or "fixture"
        minimum = node.get("minLength", 0)
        return value + ("x" * max(0, minimum - len(value)))
    # Unconstrained JSON response bodies may legitimately be null.
    return None


class AssemblyCollector:
    """Small observation boundary fake; execute() and evidence assembly stay real."""
    last = None

    def __init__(self, base_url, run_id, evidence_time=None):
        self.base = base_url
        self.run_id = run_id
        self.evidence_time = evidence_time
        self.traces = []
        self.sequence = 0
        self.container_id = None
        AssemblyCollector.last = self

    def internal(self, method, path, **kwargs):
        if path == "/internal/evidence/journal":
            return []
        return {}


class PublicWorkloadEvidenceAssemblyTest(unittest.TestCase):
    def test_public_workload_assembly_passes_real_schema_and_semantic_validator(self):
        """Drive the public plan through execute and its real validator process."""
        schema = json.loads((ROOT / "tb4_artifacts/candidate_evidence.schema.json").read_text())
        properties = schema["properties"]
        http_schema = schema["$defs"]["http"]
        journal_schema = properties["operation_journal"]["items"]
        journals = []
        states = {}
        interval_times = {"live_backfill": 120, "cutover": 320}

        def trace(collector, method, route, phase, at_ms, status, account=None,
                  amount=None, operation_id=None, idempotency_key=None, body=None,
                  origin="candidate", container_id="old-app"):
            collector.sequence += 1
            trace_id = "assembly-trace-%04d" % collector.sequence
            row = schema_specimen(http_schema, schema, trace_id)
            row.update({
                "trace_id": trace_id, "comparison_id": None, "origin": origin,
                "phase": phase, "method": method, "route_template": route,
                "started_ms": at_ms, "completed_ms": at_ms + 1,
                "status": status, "body": body, "transport_error": None,
                "expected_domain_error": status >= 400,
                "request_account_number": account, "request_amount": amount,
                "request_operation_id": operation_id,
                "request_idempotency_key": idempotency_key,
                "container_id": container_id,
            })
            if origin == "candidate":
                row["collector_kind"] = "public_route"
                row["actual_request_path"] = (route if account is None else
                    route.replace("{accountNumber}",
                                  urllib.parse.quote(account, safe="")))
            else:
                row["collector_kind"] = "jpa_source_observation"
                row["actual_request_path"] = "/internal/evidence/source-observation"
            collector.traces.append(row)
            return row

        def journal(row, kind, result, amount):
            item = schema_specimen(journal_schema, schema, row["trace_id"])
            item.update({
                "operation_id": row["request_operation_id"],
                "idempotency_key": row["request_idempotency_key"],
                "account_number": row["request_account_number"],
                "kind": kind, "amount": amount, "result": result,
                "acknowledged": True, "request_trace_id": row["trace_id"],
                "started_ms": row["started_ms"],
                "completed_ms": row["completed_ms"],
                "linearization_index": len(journals) + 1,
            })
            journals.append(item)

        def score_for(full_name, balance):
            length = len(full_name.replace(" ", ""))
            name_factor = 3 if length <= 5 else length * 3 if length > 10 else length
            aggregation = name_factor + balance + 5
            return "C" if aggregation <= 0 else "B" if aggregation % 2 == 0 else "A"

        def seed(_collector, number, _identity, balance):
            full_name = "Holder " + number
            states[number] = {"accountNumber": number,
                              "fullName": full_name, "balance": balance,
                              "score": score_for(full_name, balance),
                              "appliedOperationIds": []}

        def financial(collector, operation, phase, accounts):
            kind = operation["kind"]
            if kind == "read":
                number = accounts[operation["account"]]
                trace(collector, "GET", runner.ROUTES["read"][1], phase,
                      interval_times[phase] + 40, 200, number,
                      body={key: states[number][key]
                            for key in ("accountNumber", "fullName", "balance", "score")})
                return
            number = accounts.get(operation["account"], operation["account"])
            amount = operation["amount"]
            operation_id = operation["operation_id"]
            idem = operation.get("idempotency_key", operation_id)
            result, status = "applied", 204
            if operation_id == "fund-04":
                result = "duplicate_ignored"
            elif number not in states:
                result, status = "account_not_found", 404
            elif kind == "withdraw" and states[number]["balance"] < amount:
                result, status = "insufficient_funds", 409
            elif kind == "deposit":
                states[number]["balance"] += amount
                states[number]["appliedOperationIds"].append(operation_id)
            else:
                states[number]["balance"] -= amount
                states[number]["appliedOperationIds"].append(operation_id)
            if result == "applied":
                states[number]["score"] = score_for(
                    states[number]["fullName"], states[number]["balance"])
            at_ms = interval_times[phase] + 5 + len(journals) * 3
            row = trace(collector, "POST", runner.ROUTES[kind][1], phase, at_ms,
                        status, number, amount, operation_id, idem)
            journal(row, kind, result, amount)

        def wait_interval(collector, kind, _account=None):
            phase = "live_backfill" if kind == "backfill" else "cutover"
            at_ms = 180 if kind == "backfill" else 390
            for number in states:
                body = {key: states[number][key]
                        for key in ("accountNumber", "fullName", "balance", "score")}
                trace(collector, "GET", runner.ROUTES["read"][1], phase,
                      at_ms, 200, number, body=body)
                at_ms += 2
            return {"intervals": [{"intervalId": "backfill-1", "kind": "backfill",
                                    "startedMs": 100, "completedMs": 200},
                                   {"intervalId": "cutover-1", "kind": "cutover",
                                    "startedMs": 300, "completedMs": 400}]}

        def latency_samples(collector, phase, origin, _accounts, _national_id):
            result = {}
            for method, route in runner.ROUTES.values():
                ids = []
                for index in range(20):
                    at_ms = 10 + index * 3
                    account = (None if route == runner.ROUTES["create"][1] else
                               (next(iter(_accounts.values())) if method == "GET"
                                else "latency-missing"))
                    if origin == "source":
                        public = trace(collector, method, route, phase, at_ms,
                                       404, account=account, origin="candidate")
                        comparison = "latency-comparison-" + public["trace_id"]
                        public["comparison_id"] = comparison
                        row = trace(collector, method, route, phase, at_ms + 2,
                                    404, account=account, origin="source")
                        row["comparison_id"] = comparison
                    else:
                        row = trace(collector, method, route, phase, at_ms,
                                    404, account=account, origin=origin)
                    ids.append(row["trace_id"])
                result[(method, route)] = ids
            return result

        def replacement(collector, accounts, operation_id, heldout_creation, workload_clock):
            numbers = list(accounts.values())
            restart = []
            for index, number in enumerate(numbers[:3]):
                body = {key: states[number][key]
                        for key in ("accountNumber", "fullName", "balance", "score")}
                restart.append(trace(collector, "GET", runner.ROUTES["read"][1],
                                     "target_only_restart", 530 + index * 2, 200,
                                     number, body=body, container_id="new-app")["trace_id"])
            number = numbers[0]
            post = trace(collector, "POST", runner.ROUTES["deposit"][1],
                         "target_only_restart", 540, 204, number, 1,
                         operation_id, operation_id, container_id="new-app")
            states[number]["balance"] += 1
            states[number]["score"] = score_for(
                states[number]["fullName"], states[number]["balance"])
            states[number]["appliedOperationIds"].append(operation_id)
            journal(post, "deposit", "applied", 1)
            restart.append(post["trace_id"])
            body = {key: states[number][key]
                    for key in ("accountNumber", "fullName", "balance", "score")}
            restart.append(trace(collector, "GET", runner.ROUTES["read"][1],
                                 "target_only_restart", 550, 200, number,
                                 body=body, container_id="new-app")["trace_id"])
            mount = {"volume_id": "mongo-volume",
                     "source": "/var/lib/docker/volumes/mongo-volume",
                     "destination": "/data/db"}
            return {
                "old_container_id": "old-app", "new_container_id": "new-app",
                "image_digest": "sha256:assembly",
                "container_events": [
                    {"event": "destroy", "container_id": "old-app", "at_ms": 510,
                     "image_digest": "sha256:assembly"},
                    {"event": "create", "container_id": "new-app", "at_ms": 520,
                     "image_digest": "sha256:assembly"}],
                "target_mount_before": mount, "target_mount_after": dict(mount),
                "postgresql_network_inspect": {
                    "network_id": "target-network", "network": "target-network",
                    "application_container_id": "new-app",
                    "postgresql_container_id": "postgres-app",
                    "attached_container_ids": ["new-app"],
                    "attached": False, "source_network_denied": True,
                    "inspect_command": "docker network inspect target-network"},
                "effective_configuration": {
                    "storage": "mongodb", "postgresql_enabled": False,
                    "active_profiles": ["target-only"],
                    "selected_storage_bean": "mongoBankAccountStorage"},
                "connection_observer": {
                    "complete": True, "continuous": True,
                    "application_container_id": "new-app",
                    "source_container_id": "postgres-app",
                    "started_ms": 500, "completed_ms": 560,
                    "socket_sample_count": 2, "postgresql_session_sample_count": 2,
                    "postgres_established_socket_count": 0,
                    "replacement_postgresql_session_count": 0,
                    "postgres_log_line_count": 0,
                    "observed_network_ids": ["target-network"],
                    "source_network_ids": ["source-network"],
                    "proc_net_sha256": "a" * 64,
                    "pg_stat_activity_sha256": "b" * 64,
                    "application_log_sha256": "c" * 64,
                    "connection_observations_lack_captured_content_digests": False},
                "connection_attempt_log": [], "restart_trace_ids": restart,
            }

        def inspect(reference):
            if reference == "banking-app":
                return {"Id": "old-app"}
            if reference == "mongodb":
                return {"Mounts": [{"Destination": "/data/db", "Name": "mongo-volume",
                                     "Source": "/var/lib/docker/volumes/mongo-volume"}]}
            raise AssertionError("unexpected inspection: " + reference)

        def mongo_accounts():
            return [dict(row, appliedOperationIds=list(row["appliedOperationIds"]))
                    for row in states.values()]

        timestamps = iter((1, 500, 600))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            args = types.SimpleNamespace(
                workload=str(ROOT / "tb4_artifacts/public_workloads.json"),
                output=str(output), base_url="http://candidate")
            with mock.patch.object(runner, "Collector", AssemblyCollector), \
                    mock.patch.object(runner, "now_ms", side_effect=lambda: next(timestamps)), \
                    mock.patch.object(runner, "docker_inspect", side_effect=inspect), \
                    mock.patch.object(runner, "open_seed_account", side_effect=seed), \
                    mock.patch.object(runner, "lifecycle_observation"), \
                    mock.patch.object(runner, "perform_financial", side_effect=financial), \
                    mock.patch.object(runner, "wait_interval", side_effect=wait_interval), \
                    mock.patch.object(runner, "latency_samples", side_effect=latency_samples), \
                    mock.patch.object(runner, "replace_application", side_effect=replacement), \
                    mock.patch.object(runner, "normalize_journal", return_value=journals), \
                    mock.patch.object(runner, "direct_mongo_accounts", side_effect=mongo_accounts):
                runner.execute(args)

            evidence = json.loads(output.read_text())
            phases = {row["phase"] for row in evidence["http_trace"]}
            self.assertIn("target_only", phases)
            self.assertNotIn("post_cutover", phases)
            self.assertEqual(
                {"pre_replacement", "final_reconciliation"},
                {row["purpose"] for row in evidence["mongo_snapshots"]})
            self.assertTrue(all(row["query"] == "direct_mongodb_query"
                                for row in evidence["mongo_snapshots"]))

if __name__ == "__main__":
    unittest.main()
