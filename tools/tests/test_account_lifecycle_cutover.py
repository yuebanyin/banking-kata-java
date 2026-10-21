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

    def test_missing_observation_is_rejected_instead_of_synthesized(self):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "required": ["complete", "observations"],
            "properties": {
                "complete": {"const": True},
                "observations": {"type": "array", "minItems": 1}
            },
            "additionalProperties": False
        }
        observed = {}
        with self.assertRaisesRegex(ValueError, "incomplete observed evidence"):
            runner.assert_schema_complete(observed, schema)
        self.assertEqual({}, observed)

    def test_strict_schema_check_accepts_complete_observations_unchanged(self):
        schema = {"type": "object", "required": ["observed"],
                  "properties": {"observed": {"type": "boolean"}}}
        observed = {"observed": False}
        self.assertIs(observed, runner.assert_schema_complete(observed, schema))
        self.assertEqual({"observed": False}, observed)

    def test_public_workload_is_derived(self):
        workload = json.loads((ROOT / "tb4_artifacts/public_workloads.json").read_text())
        plan = runner.derive_plan(workload)
        self.assertTrue(plan["observations"])
        self.assertTrue(plan["waves"])
        self.assertTrue(plan["aliases"])


class WorkloadBindingTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        path = ROOT / "tools" / "validate_account_lifecycle_evidence.py"
        spec = importlib.util.spec_from_file_location("cutover_validator", path)
        self.validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.validator)
        self.evidence = {
            "http_trace": [{
                "origin": "candidate", "request_account_number": "account-a",
                "request_operation_id": "operation-a", "request_amount": 25,
                "request_idempotency_key": "operation-a"
            }],
            "operation_journal": [{
                "operation_id": "operation-a", "idempotency_key": "operation-a",
                "account_number": "account-a", "amount": 25, "kind": "deposit"
            }],
        }
        self.workload = {
            "controlled_collaborators": {"account_numbers": ["account-a"]},
            "operations": [{
                "operation_id": "operation-a", "idempotency_key": "operation-a",
                "account_number": "account-a", "amount": 25, "kind": "deposit"
            }],
        }

    def test_evidence_is_bound_to_declared_workload_identifiers_and_payload(self):
        self.validator.validate_workload_binding(self.evidence, self.workload)

    def test_stale_evidence_from_another_workload_is_rejected(self):
        self.workload["controlled_collaborators"]["account_numbers"] = ["held-out-account"]
        with self.assertRaisesRegex(ValueError, "does not match workload"):
            self.validator.validate_workload_binding(self.evidence, self.workload)

    def test_account_identifier_in_provenance_does_not_bind_workload(self):
        self.workload["controlled_collaborators"]["account_numbers"] = ["held-out-account"]
        self.evidence["run_id"] = "held-out-account"
        with self.assertRaisesRegex(ValueError, "account identifiers"):
            self.validator.validate_workload_binding(self.evidence, self.workload)

    def test_operation_identifier_in_provenance_does_not_bind_workload(self):
        self.workload["operations"][0]["operation_id"] = "held-out-operation"
        self.evidence["run_id"] = "held-out-operation"
        with self.assertRaisesRegex(ValueError, "operation identifier"):
            self.validator.validate_workload_binding(self.evidence, self.workload)

    def test_changed_operation_amount_is_rejected(self):
        self.workload["operations"][0]["amount"] = 26
        with self.assertRaisesRegex(ValueError, "amount mismatch"):
            self.validator.validate_workload_binding(self.evidence, self.workload)

class DeploymentObservationTest(unittest.TestCase):
    def test_runner_replacement_environment_is_explicitly_target_only_mongodb(self):
        environment = runner.replacement_environment()
        self.assertTrue(runner.is_target_only_mongo_environment(environment))
        self.assertFalse(any("adapter-persistence-jpa" in item for item in environment))

    def test_mongo_profile_without_target_only_mode_is_not_a_complete_audit(self):
        environment = [
            "SPRING_PROFILES_ACTIVE=adapter-persistence-mongo,adapter-auth-none",
            "CUTOVER_EFFECTIVE_STORAGE=mongodb",
        ]
        self.assertFalse(runner.is_target_only_mongo_environment(environment))

    def test_jpa_profile_is_never_accepted_as_target_only(self):
        environment = [
            "SPRING_PROFILES_ACTIVE=adapter-persistence-mongo,target-only,adapter-persistence-jpa",
            "CUTOVER_EFFECTIVE_STORAGE=mongodb",
        ]
        self.assertFalse(runner.is_target_only_mongo_environment(environment))

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


if __name__ == "__main__":
    unittest.main()
