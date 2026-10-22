import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class FinalOnlyAccountProvenanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "tools" / "validate_account_lifecycle_evidence.py"
        spec = importlib.util.spec_from_file_location("cutover_validator_final_only", path)
        cls.validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.validator)

    def evidence(self):
        return {
            "mongo_snapshots": [
                {"purpose": "pre_replacement", "captured_ms": 200,
                 "volume_id": "mongo-volume", "accounts": []},
                {"purpose": "final_reconciliation", "captured_ms": 300,
                 "volume_id": "mongo-volume", "accounts": [
                     {"accountNumber": "ACC-NEW", "fullName": "John Smith",
                      "balance": 0, "score": "B",
                      "appliedOperationIds": ["create-new"]}
                 ]},
            ],
            "deployment_evidence": {
                "container_events": [{"event": "destroy", "at_ms": 220},
                                     {"event": "create", "at_ms": 230}],
                "target_mount_before": {"volume_id": "mongo-volume"},
                "target_mount_after": {"volume_id": "mongo-volume"},
                "restart_trace_ids": ["read-new"],
            },
        }

    def rows(self, result="applied"):
        create = {"trace_id": "create-new-trace", "origin": "candidate",
                  "method": "POST", "route_template": "/bank-accounts",
                  "status": 201, "started_ms": 240, "completed_ms": 245,
                  "body": {"accountNumber": "ACC-NEW"},
                  "request_operation_id": "create-new",
                  "request_idempotency_key": "create-new-key"}
        read = {"trace_id": "read-new", "origin": "candidate", "method": "GET",
                "status": 200, "started_ms": 250, "completed_ms": 255,
                "request_account_number": "ACC-NEW",
                "body": {"accountNumber": "ACC-NEW", "fullName": "John Smith",
                         "balance": 0, "score": "B"}}
        journal = {"operation_id": "create-new", "idempotency_key": "create-new-key",
                   "request_trace_id": "create-new-trace", "kind": "create",
                   "account_number": "ACC-NEW", "amount": 0,
                   "acknowledged": True, "result": result,
                   "started_ms": 240, "completed_ms": 245}
        source_truth = {"trace_id": "source-truth-new", "origin": "source",
                        "method": "GET",
                        "route_template": "/bank-accounts/{accountNumber}",
                        "status": 200, "started_ms": 246, "completed_ms": 249,
                        "request_account_number": "ACC-NEW",
                        "body": {"accountNumber": "ACC-NEW", "fullName": "John Smith",
                                 "balance": 0, "score": "B"}}
        source_truth["comparison_id"] = None
        source_truth["request_operation_id"] = journal["operation_id"]
        source_truth["request_idempotency_key"] = journal["idempotency_key"]
        inputs = {
            "account_number": "ACC-NEW",
            "national_identity_number": "IDENTITY-NEW",
            "initial_balance": 0,
            "controlled_clock": "2025-01-01T00:00:00Z",
            "controlled_first_name": "John",
            "controlled_last_name": "Smith",
        }
        create.update({
            "phase": "target_only", "collector_kind": "public_route",
            "actual_request_path": "/bank-accounts",
            "request_body": {"nationalIdentityNumber": "IDENTITY-NEW", "balance": 0},
            "controlled_creation_inputs": inputs,
        })
        source_truth.update({
            "phase": "target_only", "collector_kind": "fixture_creation_truth",
            "actual_request_path":
                "/internal/cutover/fixtures/creation-truth/ACC-NEW"
                "?nationalIdentityNumber=IDENTITY-NEW&balance=0"
                "&controlledClock=2025-01-01T00%3A00%3A00Z"
                "&firstName=John&lastName=Smith",
            "controlled_creation_inputs": dict(inputs),
        })
        return [journal], [create, source_truth, read]

    def test_accepts_one_linked_applied_creation_between_snapshots(self):
        journals, traces = self.rows()
        self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_recomputes_final_only_score_after_later_deposit(self):
        evidence = self.evidence()
        final = evidence["mongo_snapshots"][1]["accounts"][0]
        final.update({"balance": 1, "score": "A",
                      "appliedOperationIds": ["create-new", "deposit-new"]})
        journals, traces = self.rows()
        journals.append({"operation_id": "deposit-new", "idempotency_key": "deposit-new-key",
                         "request_trace_id": "deposit-new-trace", "kind": "deposit",
                         "account_number": "ACC-NEW", "amount": 1, "acknowledged": True,
                         "result": "applied", "started_ms": 260, "completed_ms": 265})
        traces.append({"trace_id": "deposit-new-trace", "origin": "candidate",
                       "method": "POST", "route_template": "/bank-accounts/{accountNumber}/deposits",
                       "status": 204, "started_ms": 260, "completed_ms": 265, "body": None,
                       "request_account_number": "ACC-NEW", "request_amount": 1,
                       "request_operation_id": "deposit-new",
                       "request_idempotency_key": "deposit-new-key"})
        traces.append({"trace_id": "read-after-deposit", "origin": "candidate",
                       "method": "GET", "status": 200, "started_ms": 270,
                       "completed_ms": 275, "request_account_number": "ACC-NEW",
                       "body": {"accountNumber": "ACC-NEW", "fullName": "John Smith",
                                "balance": 1, "score": "A"}})
        self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_rejects_duplicate_only_creation_between_snapshots(self):
        evidence = self.evidence()
        evidence["mongo_snapshots"][1]["accounts"][0]["appliedOperationIds"] = []
        journals, traces = self.rows("duplicate_ignored")
        with self.assertRaisesRegex(ValueError, "applied creation journal"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_rejects_self_corroborating_snapshot_and_get_without_creation(self):
        evidence = self.evidence()
        evidence["mongo_snapshots"][1]["accounts"][0]["appliedOperationIds"] = []
        journals, traces = self.rows()
        with self.assertRaisesRegex(ValueError, "exactly one successful creation POST"):
            self.validator.validate_mongo_snapshots(evidence, [], traces[2:])


    def test_rejects_matching_forged_final_snapshot_and_restart_get(self):
        evidence = self.evidence()
        final = evidence["mongo_snapshots"][1]["accounts"][0]
        final.update({"fullName": "Forged Holder", "balance": 777, "score": "A"})
        journals, traces = self.rows()
        traces[-1]["body"].update({"fullName": "Forged Holder", "balance": 777,
                                   "score": "A"})
        with self.assertRaisesRegex(ValueError, "controlled"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)

    def test_rejects_creation_operation_identity_mismatch(self):
        journals, traces = self.rows()
        traces[0]["request_operation_id"] = "forged-operation"
        with self.assertRaisesRegex(ValueError, "operation identity mismatch"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_rejects_two_successful_creations_for_final_only_account(self):
        journals, traces = self.rows()
        duplicate = dict(traces[0], trace_id="second-create", started_ms=246,
                         completed_ms=247, request_operation_id="second-operation")
        traces.append(duplicate)
        with self.assertRaisesRegex(ValueError, "exactly one successful creation POST"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_rejects_creation_outside_snapshot_window(self):
        journals, traces = self.rows()
        traces[0]["started_ms"] = 199
        journals[0]["started_ms"] = 199
        with self.assertRaisesRegex(ValueError, "exactly one successful creation POST"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)


    def test_rejects_truth_not_bound_to_the_creation_operation(self):
        journals, traces = self.rows()
        traces[1]["request_operation_id"] = "unrelated-operation"
        with self.assertRaisesRegex(ValueError, "fixture creation truth"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)



    def test_rejects_mislabeled_or_arbitrary_creation_truth(self):
        for key, value in (("collector_kind", "public_route"),
                           ("actual_request_path", "/bank-accounts/ACC-NEW")):
            journals, traces = self.rows()
            traces[1][key] = value
            with self.assertRaisesRegex(ValueError, "fixture creation truth"):
                self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_rejects_jpa_provenance_for_target_only_creation_truth(self):
        journals, traces = self.rows()
        traces[1]["collector_kind"] = "jpa_source_observation"
        traces[1]["actual_request_path"] = "/internal/evidence/source-observation"
        with self.assertRaisesRegex(ValueError, "JPA provenance is prohibited"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_rejects_mongo_backed_truth_masquerading_as_source(self):
        journals, traces = self.rows()
        traces[1]["collector_kind"] = "public_route"
        traces[1]["actual_request_path"] = "/bank-accounts/ACC-NEW"
        with self.assertRaisesRegex(ValueError, "fixture creation truth"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

    def test_rejects_truth_inputs_not_bound_to_public_post(self):
        journals, traces = self.rows()
        traces[1]["controlled_creation_inputs"] = dict(
            traces[1]["controlled_creation_inputs"],
            national_identity_number="WRONG")
        with self.assertRaisesRegex(ValueError, "fixture creation truth"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)

        journals, traces = self.rows()
        traces[0]["request_body"]["nationalIdentityNumber"] = "WRONG"
        with self.assertRaisesRegex(ValueError, "controlled identity input mismatch"):
            self.validator.validate_mongo_snapshots(self.evidence(), journals, traces)


    def test_rejects_matching_forged_fixture_snapshot_and_restart_get(self):
        evidence = self.evidence()
        final = evidence["mongo_snapshots"][1]["accounts"][0]
        final.update({"fullName": "Forged Holder", "score": "A"})
        journals, traces = self.rows()
        traces[1]["body"].update({"fullName": "Forged Holder", "score": "A"})
        traces[2]["body"].update({"fullName": "Forged Holder", "score": "A"})
        with self.assertRaisesRegex(ValueError, "controlled inputs"):
            self.validator.validate_mongo_snapshots(evidence, journals, traces)


if __name__ == "__main__":
    unittest.main()
