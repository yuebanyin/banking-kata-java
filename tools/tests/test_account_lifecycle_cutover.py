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

    def test_direct_mongo_snapshot_ignores_journal_collection_documents(self):
        from unittest.mock import patch

        collections = {
            "bank_account": [{
                "accountNumber": "ACC-1", "fullName": "Ada Holder",
                "balance": 42, "score": "B",
                "appliedOperationIds": ["create-1", "deposit-1"],
            }],
            "account_operation_journal": [{
                "operationId": "deposit-1", "accountNumber": "ACC-1",
                "kind": "deposit", "result": "applied",
            }],
        }

        def fake_mongosh(command, **_kwargs):
            script = command[command.index("--eval") + 1]
            self.assertIn('db.getCollection("bank_account")', script)
            self.assertNotIn("getCollectionNames", script)
            # Emulate mongosh executing the explicitly named collection query.
            return json.dumps(collections["bank_account"])

        with patch.object(runner, "run", side_effect=fake_mongosh):
            snapshot = runner.direct_mongo_accounts("mongodb")

        self.assertEqual(snapshot, [{
            "accountNumber": "ACC-1", "fullName": "Ada Holder",
            "balance": 42, "score": "B",
            "appliedOperationIds": ["create-1", "deposit-1"],
        }])

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

    def test_financial_account_alias_is_bound_to_mapped_http_journal_and_snapshot_account(self):
        import copy
        workload = {
            "fixture_seed": 71,
            "controlled_collaborators": {"account_numbers": []},
            "contract_families": [{
                "family_id": "funds-linearizability",
                "initial_balances": {"hot": 100, "exact": 50},
                "waves": [{"operations": [{"kind": "deposit", "account": "exact",
                                               "amount": 25, "operation_id": "alias-op"}]}]
            }]
        }
        aliases = self.validator._financial_alias_accounts(workload)
        stale = {
            "http_trace": [{"origin": "candidate", "request_operation_id": "alias-op",
                            "request_account_number": aliases["hot"], "request_amount": 25,
                            "request_idempotency_key": None}],
            "operation_journal": [{"operation_id": "alias-op", "idempotency_key": "alias-op",
                                   "account_number": aliases["hot"], "amount": 25,
                                   "kind": "deposit"}],
            "mongo_snapshots": [{"accounts": [{"accountNumber": aliases["hot"]}]}]
        }
        with self.assertRaisesRegex(ValueError, "account mismatch"):
            self.validator.validate_workload_binding(stale, workload)

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

    def test_source_isolation_is_derived_from_scoped_network_socket_session_and_config(self):
        app = {"Id": "app-new", "Config": {"Env": runner.replacement_environment()},
               "NetworkSettings": {"Networks": {"target": {"NetworkID": "network-target",
                                                               "IPAddress": "172.20.0.5"}}}}
        source = {"Id": "postgres-project", "NetworkSettings": {"Networks": {
            "source-admin": {"NetworkID": "network-source", "IPAddress": "172.21.0.2"}}}}
        effective = {"storage": "mongodb", "postgresql_enabled": False,
                     "active_profiles": ["adapter-persistence-mongo", "target-only"],
                     "selected_storage_bean": "example.MongoBankAccountStorage"}
        observer, attempts = runner.assess_source_isolation(
            app, source, "network-target", "  sl local_address rem_address st\n",
            "172.21.0.9|psql|active\n", "mongo startup complete\n", effective, 100)
        self.assertTrue(observer["complete"])
        self.assertEqual(observer["postgres_established_socket_count"], 0)
        self.assertEqual(attempts, [])

        observer, _ = runner.assess_source_isolation(
            app, source, "network-target",
            "0: 0100007F:C001 0200007F:1538 01 00000000:00000000\n",
            "", "", effective, 100)
        self.assertFalse(observer["complete"])
        self.assertEqual(observer["postgres_established_socket_count"], 1)

    def test_scoped_compose_lookup_rejects_unrelated_global_container(self):
        from unittest.mock import patch
        unrelated = {"Id": "other-postgres", "Config": {"Labels": {
            "com.docker.compose.service": "postgres",
            "com.docker.compose.project": "some-other-project"}}}
        with patch.object(runner, "run", return_value="other-postgres"), \
             patch.object(runner, "docker_inspect", return_value=unrelated):
            with self.assertRaisesRegex(RuntimeError, "another Compose project"):
                runner.scoped_compose_container("postgres", "banking-project")


class MongoSnapshotReconciliationTest(unittest.TestCase):
    def setUp(self):
        path = ROOT / "tools" / "validate_account_lifecycle_evidence.py"
        spec = importlib.util.spec_from_file_location("cutover_validator", path)
        self.validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.validator)

    def evidence(self):
        account = {"accountNumber": "ACC-1", "fullName": "Ada Holder",
                   "balance": 50, "score": "B", "appliedOperationIds": ["deposit-1"]}
        return {
            "mongo_snapshots": [
                {"purpose": "pre_replacement", "captured_ms": 200,
                 "volume_id": "mongo-volume", "accounts": [dict(account)]},
                {"purpose": "final_reconciliation", "captured_ms": 300,
                 "volume_id": "mongo-volume", "accounts": [dict(account)]},
            ],
            "deployment_evidence": {
                "container_events": [{"event": "destroy", "at_ms": 220},
                                     {"event": "create", "at_ms": 230}],
                "target_mount_before": {"volume_id": "mongo-volume"},
                "target_mount_after": {"volume_id": "mongo-volume"},
                "restart_trace_ids": ["read-1"],
            },
        }

    def rows(self):
        traces = [{"trace_id": "read-1", "origin": "candidate", "method": "GET",
                   "status": 200, "completed_ms": 190, "request_account_number": "ACC-1",
                   "body": {"accountNumber": "ACC-1", "fullName": "Ada Holder",
                            "balance": 42, "score": "B"}}]
        journals = [{"operation_id": "deposit-1", "account_number": "ACC-1",
                     "kind": "deposit", "amount": 8, "acknowledged": True,
                     "result": "applied", "completed_ms": 195}]
        return journals, traces

    def test_reconciles_pre_and_final_identity_state_operations_and_mount(self):
        journals, traces = self.rows()
        self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_rejects_corrupt_pre_replacement_state(self):
        evidence = self.evidence()
        evidence["mongo_snapshots"][0]["accounts"][0]["fullName"] = "Forged"
        journals, traces = self.rows()
        with self.assertRaisesRegex(ValueError, "fullName"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_replays_only_between_snapshot_journal_operations(self):
        evidence = self.evidence()
        final = evidence["mongo_snapshots"][1]["accounts"][0]
        final["balance"] = 55
        final["appliedOperationIds"] = ["deposit-1", "deposit-2"]
        journals, traces = self.rows()
        journals.append({"operation_id": "deposit-2", "account_number": "ACC-1",
                         "kind": "deposit", "amount": 5, "acknowledged": True,
                         "result": "applied", "completed_ms": 240})
        self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_rejects_matching_final_snapshot_and_restart_get_corruption(self):
        evidence = self.evidence()
        corrupt = evidence["mongo_snapshots"][1]["accounts"][0]
        corrupt.update({"fullName": "Corrupt Holder", "balance": 999, "score": "A"})
        journals, traces = self.rows()
        traces.append({"trace_id": "corrupt-restart-read", "origin": "candidate",
                       "method": "GET", "status": 200, "completed_ms": 250,
                       "request_account_number": "ACC-1",
                       "body": {"accountNumber": "ACC-1", "fullName": "Corrupt Holder",
                                "balance": 999, "score": "A"}})
        evidence["deployment_evidence"]["restart_trace_ids"] = ["corrupt-restart-read"]
        with self.assertRaisesRegex(ValueError, "replacement changed immutable fullName"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_rejects_snapshot_volume_not_bound_to_deployment_mount(self):
        evidence = self.evidence()
        evidence["mongo_snapshots"][1]["volume_id"] = "unrelated-volume"
        journals, traces = self.rows()
        with self.assertRaisesRegex(ValueError, "observed MongoDB mount"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_rejects_pre_replacement_timestamp_after_destroy(self):
        evidence = self.evidence()
        evidence["mongo_snapshots"][0]["captured_ms"] = 225
        journals, traces = self.rows()
        with self.assertRaisesRegex(ValueError, "after container destruction"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)


if __name__ == "__main__":
    unittest.main()
