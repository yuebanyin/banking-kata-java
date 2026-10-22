import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "account_lifecycle_cutover", ROOT / "tools" / "account_lifecycle_cutover.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class TargetOnlyCreationTruthTest(unittest.TestCase):
    def test_uses_one_persistence_independent_fixture_request(self):
        collector = runner.Collector("http://candidate", "heldout")
        calls = []

        def exchange(method, path, body, headers):
            calls.append((method, path, body, headers))
            return 200, {
                "collectorKind": "fixture_creation_truth",
                "accountNumber": "TARGET-42", "fullName": "John Smith",
                "nationalIdentityNumber": "HELDOUT-ID", "initialBalance": 0,
                "controlledClock": "2025-01-01T00:00:00Z",
                "balance": 0, "score": "C"
            }, None

        collector._exchange = exchange
        creation = {
            "trace_id": "heldout-create", "body": {"accountNumber": "TARGET-42"},
            "request_operation_id": "heldout-create-op",
            "request_idempotency_key": "heldout-create-op",
        }
        truth = collector.creation_truth(
            creation, "HELDOUT-ID", 0, "2025-01-01T00:00:00Z",
            phase="target_only")

        self.assertEqual(1, len(calls))
        self.assertEqual("GET", calls[0][0])
        self.assertIn("/internal/cutover/fixtures/creation-truth/TARGET-42", calls[0][1])
        self.assertNotIn("/internal/evidence/source-observation", calls[0][1])
        self.assertEqual("source", truth["origin"])
        self.assertIsNone(truth["comparison_id"])
        self.assertEqual("heldout-create-op", truth["request_operation_id"])
        self.assertEqual("heldout-create-op", truth["request_idempotency_key"])
        self.assertEqual("target_only", truth["phase"])
        self.assertEqual("fixture_creation_truth", truth["collector_kind"])
        self.assertEqual(calls[0][1], truth["actual_request_path"])
        self.assertEqual({
            "account_number": "TARGET-42",
            "national_identity_number": "HELDOUT-ID",
            "initial_balance": 0,
            "controlled_clock": "2025-01-01T00:00:00Z",
        }, truth["controlled_creation_inputs"])

    def test_never_replays_the_public_creation(self):
        collector = runner.Collector("http://candidate", "heldout")
        paths = []
        collector._exchange = lambda method, path, body, headers: (
            paths.append(path) or (200, {
                "collectorKind": "fixture_creation_truth",
                "accountNumber": "TARGET-7", "fullName": "John Smith",
                "nationalIdentityNumber": "IDENTITY-7", "initialBalance": 8,
                "controlledClock": "2025-01-01T00:00:00Z",
                "balance": 8, "score": "C"
            }, None))
        creation = {
            "trace_id": "heldout-create-7",
            "body": {"accountNumber": "TARGET-7"},
            "request_operation_id": "create-7",
            "request_idempotency_key": "create-7",
        }
        collector.creation_truth(creation, "IDENTITY-7", 8,
                                 "2025-01-01T00:00:00Z", phase="target_only")
        self.assertEqual(1, len(paths))
        self.assertNotEqual("/bank-accounts", paths[0])


    def test_rejects_arbitrary_or_input_mismatched_internal_response(self):
        creation = {
            "trace_id": "create", "body": {"accountNumber": "TARGET-9"},
            "request_operation_id": "operation-9",
            "request_idempotency_key": "operation-9",
        }
        for response in (
                {"accountNumber": "TARGET-9", "fullName": "Forged",
                 "balance": 777, "score": "A"},
                {"collectorKind": "fixture_creation_truth",
                 "accountNumber": "TARGET-9", "fullName": "John Smith",
                 "nationalIdentityNumber": "WRONG", "initialBalance": 0,
                 "controlledClock": "2025-01-01T00:00:00Z",
                 "balance": 0, "score": "C"}):
            collector = runner.Collector("http://candidate", "heldout")
            collector._exchange = lambda *_args, response=response: (200, response, None)
            with self.assertRaisesRegex(RuntimeError, "provenance/input mismatch"):
                collector.creation_truth(
                    creation, "IDENTITY-9", 0, "2025-01-01T00:00:00Z",
                    phase="target_only")


if __name__ == "__main__":
    unittest.main()
