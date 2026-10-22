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
                "accountNumber": "TARGET-42", "fullName": "John Smith",
                "balance": 0, "score": "C"
            }, None

        collector._exchange = exchange
        creation = {
            "trace_id": "heldout-create", "body": {"accountNumber": "TARGET-42"},
            "request_operation_id": "heldout-create-op",
            "request_idempotency_key": "heldout-create-op",
        }
        truth = collector.creation_truth(
            creation, "HELDOUT-ID", 0, phase="target_only")

        self.assertEqual(1, len(calls))
        self.assertEqual("GET", calls[0][0])
        self.assertIn("/internal/cutover/fixtures/creation-truth/TARGET-42", calls[0][1])
        self.assertNotIn("/internal/evidence/source-observation", calls[0][1])
        self.assertEqual("source", truth["origin"])
        self.assertIsNone(truth["comparison_id"])
        self.assertEqual("heldout-create-op", truth["request_operation_id"])
        self.assertEqual("heldout-create-op", truth["request_idempotency_key"])
        self.assertEqual("target_only", truth["phase"])

    def test_never_replays_the_public_creation(self):
        collector = runner.Collector("http://candidate", "heldout")
        paths = []
        collector._exchange = lambda method, path, body, headers: (
            paths.append(path) or (200, {
                "accountNumber": "TARGET-7", "fullName": "John Smith",
                "balance": 8, "score": "C"
            }, None))
        creation = {
            "trace_id": "heldout-create-7",
            "body": {"accountNumber": "TARGET-7"},
            "request_operation_id": "create-7",
            "request_idempotency_key": "create-7",
        }
        collector.creation_truth(creation, "IDENTITY-7", 8, phase="target_only")
        self.assertEqual(1, len(paths))
        self.assertNotEqual("/bank-accounts", paths[0])


if __name__ == "__main__":
    unittest.main()
