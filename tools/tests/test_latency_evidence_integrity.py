import importlib.util
import json
import subprocess
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

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


    def test_execute_removes_stale_output_before_early_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "evidence.json"
            output.write_text("stale evidence")
            args = types.SimpleNamespace(
                workload=str(root / "missing-workload.json"), output=str(output),
                base_url="http://candidate")

            with self.assertRaises(FileNotFoundError):
                runner.execute(args)

            self.assertFalse(output.exists())
            self.assertEqual([], list(root.glob(".evidence.json.*.tmp")))

    def test_validator_failure_publishes_nothing_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = root / "workload.json"
            workload.write_text("{}")
            output = root / "evidence.json"
            output.write_text("stale evidence")
            observed_temporary = []

            def reject(command, **kwargs):
                temporary = Path(command[command.index("--evidence") + 1])
                observed_temporary.append(temporary)
                self.assertNotEqual(output.resolve(), temporary)
                self.assertTrue(temporary.is_file())
                self.assertFalse(output.exists())
                self.assertEqual({"run": "new"}, json.loads(temporary.read_text()))
                raise subprocess.CalledProcessError(1, command)

            with mock.patch.object(runner.subprocess, "run", side_effect=reject):
                with self.assertRaises(subprocess.CalledProcessError):
                    runner.publish_validated_evidence(
                        {"run": "new"}, workload, output)

            self.assertEqual(1, len(observed_temporary))
            self.assertFalse(output.exists())
            self.assertFalse(observed_temporary[0].exists())
            self.assertEqual([], list(root.glob(".evidence.json.*.tmp")))

    def test_success_is_atomically_published_after_temporary_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workload = root / "workload.json"
            workload.write_text("{}")
            output = root / "evidence.json"

            def accept(command, **kwargs):
                temporary = Path(command[command.index("--evidence") + 1])
                self.assertTrue(temporary.exists())
                self.assertFalse(output.exists())
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.object(runner.subprocess, "run", side_effect=accept):
                runner.publish_validated_evidence({"run": "valid"}, workload, output)

            self.assertEqual({"run": "valid"}, json.loads(output.read_text()))
            self.assertEqual([], list(root.glob(".evidence.json.*.tmp")))

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
