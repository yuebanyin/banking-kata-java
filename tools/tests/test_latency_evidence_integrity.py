import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("runner_latency", ROOT / "tools/account_lifecycle_cutover.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FakeCollector:
    run_id = "changed-run"

    def __init__(self):
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"trace_id": "trace-%03d" % len(self.calls)}


class LatencyEvidenceIntegrityTest(unittest.TestCase):
    def test_repeatable_error_probes_do_not_claim_operation_identity(self):
        collector = FakeCollector()
        runner.latency_samples(collector, "source_baseline", "source",
                               {"preexisting": "A"}, "missing-customer")
        creates = [call for call in collector.calls if call[2]["route_template"] == runner.ROUTES["create"][1]]
        self.assertEqual(20, len(creates))
        self.assertTrue(all(call[2]["operation_id"] is None for call in creates))
        self.assertTrue(all(call[2]["idempotency_key"] is None for call in creates))

    def test_successful_latency_post_without_journal_is_rejected(self):
        trace = {"trace_id": "post", "comparison_id": None, "origin": "candidate",
                 "method": "POST", "status": 201, "request_operation_id": None,
                 "request_idempotency_key": None}
        partition = [{"baseline_trace_ids": [], "post_cutover_trace_ids": ["post"]}]
        with self.assertRaisesRegex(ValueError, "exactly one journal"):
            runner.assert_latency_posts_journaled([trace], [], partition)
        trace["status"] = 500
        runner.assert_latency_posts_journaled([trace], [], partition)


if __name__ == "__main__":
    unittest.main()
