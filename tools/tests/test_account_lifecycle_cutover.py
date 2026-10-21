import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cutover_runner", ROOT / "tools/account_lifecycle_cutover.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = json.dumps({"accountNumber": "changed-account", "balance": 7}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class RunnerTests(unittest.TestCase):
    def test_derives_changed_workload_values_without_public_case_ids(self):
        workload = {
            "fixture_seed": 99,
            "controlled_collaborators": {"account_numbers": ["changed-number"]},
            "contract_families": [
                {"family_id": "account-lifecycle-http", "observations": [
                    {"case_id": "held-out", "method": "GET", "phase": "cutover",
                     "route_template": "/bank-accounts/{accountNumber}"}]},
                {"family_id": "funds-linearizability", "initial_balances": {"other": 17},
                 "waves": [{"phase": "cutover", "operations": [
                     {"kind": "deposit", "account": "other", "amount": 3,
                      "operation_id": "different-op"}]}]},
            ],
        }
        plan = runner.derive_plan(workload)
        self.assertEqual(plan["numbers"], ["changed-number"])
        self.assertEqual(plan["aliases"], {"other": 17})
        self.assertEqual(plan["waves"][0]["operations"][0]["operation_id"], "different-op")

    def test_collector_records_real_http_observation(self):
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            collector = runner.Collector("http://127.0.0.1:%d" % server.server_port, "heldout")
            trace = collector.request("GET", "/bank-accounts/changed-account",
                                      route_template=runner.ROUTES["read"][1],
                                      account="changed-account")
            self.assertEqual(trace["status"], 200)
            self.assertEqual(trace["body"]["balance"], 7)
            self.assertIsNone(trace["transport_error"])
            self.assertGreaterEqual(trace["completed_ms"], trace["started_ms"])
        finally:
            server.shutdown()
            server.server_close()

    def test_normalizes_only_journal_rows_from_this_run(self):
        rows = [{"operationId": "other", "idempotencyKey": "other",
                 "requestTraceId": "old", "kind": "deposit", "accountNumber": "a",
                 "amount": 1, "startedMs": 1, "completedMs": 2,
                 "linearizationIndex": 1, "result": "applied", "acknowledged": True},
                {"operationId": "new", "idempotencyKey": "new",
                 "requestTraceId": "current", "kind": "withdraw", "accountNumber": "a",
                 "amount": 2, "startedMs": 3, "completedMs": 4,
                 "linearizationIndex": 2, "result": "applied", "acknowledged": True}]
        normalized = runner.normalize_journal(rows, {"current"})
        self.assertEqual([row["operation_id"] for row in normalized], ["new"])
        self.assertEqual(normalized[0]["request_trace_id"], "current")

    def test_runtime_deployment_shape_conforms_to_public_schema(self):
        import jsonschema
        schema = json.loads((ROOT / "tb4_artifacts/candidate_evidence.schema.json").read_text())
        deployment_schema = schema["properties"]["deployment_evidence"]
        value = {
            "old_container_id": "old-container", "new_container_id": "new-container",
            "image_digest": "sha256:" + "a" * 64,
            "container_events": [
                {"event": "destroy", "container_id": "old-container", "at_ms": 1,
                 "image_digest": "sha256:" + "a" * 64},
                {"event": "create", "container_id": "new-container", "at_ms": 2,
                 "image_digest": "sha256:" + "a" * 64}],
            "target_mount_before": "/volume", "target_mount_after": "/volume",
            "postgresql_network_inspect": {"network": "cutover", "attached": False,
                                            "captured_ms": 3},
            "effective_configuration": {"storage": "mongodb", "postgresql_enabled": False,
                                         "active_profiles": ["mongodb"],
                                         "selected_storage_bean": "mongoBankAccountStorage"},
            "connection_observer": {"kind": "docker-network-and-log", "complete": True,
                                    "started_ms": 1, "completed_ms": 3},
            "connection_attempt_log": [], "restart_trace_ids": ["one", "two", "three", "four"]}
        value = runner.complete_schema(value, deployment_schema, schema)
        jsonschema.Draft202012Validator({"$schema": schema["$schema"], "$defs": schema["$defs"],
                                             **deployment_schema}).validate(value)
        self.assertEqual(value["effective_configuration"]["storage"], "mongodb")
        self.assertFalse(value["effective_configuration"]["postgresql_enabled"])

    def test_public_workload_is_derived(self):
        workload = json.loads((ROOT / "tb4_artifacts/public_workloads.json").read_text())
        plan = runner.derive_plan(workload)
        self.assertTrue(plan["observations"])
        self.assertTrue(plan["waves"])
        self.assertTrue(plan["aliases"])


if __name__ == "__main__":
    unittest.main()

class WorkloadBindingTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        path = ROOT / "tools" / "validate_account_lifecycle_evidence.py"
        spec = importlib.util.spec_from_file_location("cutover_validator", path)
        self.validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.validator)
        self.evidence = {
            "http_trace": [{"request_account_number": "account-a", "request_operation_id": "operation-a"}],
            "operation_journal": [{
                "operation_id": "operation-a", "account_number": "account-a",
                "amount": 25, "kind": "deposit"
            }],
        }
        self.workload = {
            "controlled_collaborators": {"account_numbers": ["account-a"]},
            "operations": [{
                "operation_id": "operation-a", "account_number": "account-a",
                "amount": 25, "kind": "deposit"
            }],
        }

    def test_evidence_is_bound_to_declared_workload_identifiers_and_payload(self):
        self.validator.validate_workload_binding(self.evidence, self.workload)

    def test_stale_evidence_from_another_workload_is_rejected(self):
        self.workload["controlled_collaborators"]["account_numbers"] = ["held-out-account"]
        with self.assertRaisesRegex(ValueError, "does not match workload"):
            self.validator.validate_workload_binding(self.evidence, self.workload)

    def test_changed_operation_amount_is_rejected(self):
        self.workload["operations"][0]["amount"] = 26
        with self.assertRaisesRegex(ValueError, "amount mismatch"):
            self.validator.validate_workload_binding(self.evidence, self.workload)

class DeploymentObservationTest(unittest.TestCase):
    def test_network_evidence_uses_daemon_observed_id(self):
        import importlib.util
        import subprocess
        from unittest.mock import patch
        path = ROOT / "tools" / "account_lifecycle_cutover.py"
        spec = importlib.util.spec_from_file_location("cutover_runner_network", path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        response = subprocess.CompletedProcess([], 0, '[{"Id":"network-sha-123"}]', "")
        with patch.object(runner, "run", return_value=response):
            self.assertEqual("network-sha-123", runner.observed_network_id("project_target"))
