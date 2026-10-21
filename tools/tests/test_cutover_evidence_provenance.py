import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("cutover_runner_provenance", "tools/account_lifecycle_cutover.py")
validator = load("cutover_validator_provenance", "tools/validate_account_lifecycle_evidence.py")


class Response:
    def __init__(self, status, body=b""):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CollectorSeparationTest(unittest.TestCase):
    def test_mutating_pair_executes_public_request_once_then_source_receipt(self):
        urls = []

        def open_request(request, timeout):
            urls.append(request.full_url)
            if request.full_url.endswith("/internal/evidence/source-observation"):
                payload = json.loads(request.data)
                self.assertEqual("POST", payload["method"])
                self.assertEqual(204, payload["candidateStatus"])
                return Response(200, b'{"status":204,"body":null}')
            return Response(204)

        collector = runner.Collector("http://candidate", "run")
        with mock.patch.object(runner.urllib.request, "urlopen", side_effect=open_request):
            candidate, source = runner.paired(
                collector, "POST", "/bank-accounts/A/deposits",
                phase="live_backfill", route_template=runner.ROUTES["deposit"][1],
                body={"amount": 2, "operationId": "op"}, account="A", amount=2,
                operation_id="op", idempotency_key="op")

        self.assertEqual(1, urls.count("http://candidate/bank-accounts/A/deposits"))
        self.assertEqual(1, urls.count("http://candidate/internal/evidence/source-observation"))
        self.assertEqual("candidate", candidate["origin"])
        self.assertEqual("source", source["origin"])
        self.assertGreaterEqual(source["started_ms"], candidate["completed_ms"])

    def test_source_baseline_uses_one_public_request_and_one_jpa_receipt(self):
        urls = []
        def open_request(request, timeout):
            urls.append(request.full_url)
            if request.full_url.endswith("/internal/evidence/source-observation"):
                return Response(200, b'{"status":204,"body":null}')
            return Response(204)
        collector = runner.Collector("http://candidate", "run")
        with mock.patch.object(runner.urllib.request, "urlopen", side_effect=open_request):
            source = collector.request(
                "POST", "/bank-accounts/A/deposits", origin="source",
                route_template=runner.ROUTES["deposit"][1], account="A", amount=1,
                operation_id="op", idempotency_key="op", body={"amount": 1})
        self.assertEqual("source", source["origin"])
        self.assertEqual(1, urls.count("http://candidate/bank-accounts/A/deposits"))
        self.assertEqual(1, urls.count("http://candidate/internal/evidence/source-observation"))


class DeploymentChronologyTest(unittest.TestCase):
    def evidence(self):
        return {
            "http_trace": [
                {"trace_id": "candidate", "comparison_id": "pair", "origin": "candidate",
                 "started_ms": 10, "completed_ms": 11, "container_id": "old"},
                {"trace_id": "source", "comparison_id": "pair", "origin": "source",
                 "started_ms": 12, "completed_ms": 13, "container_id": "old"},
                {"trace_id": "restart-read", "comparison_id": None, "origin": "candidate",
                 "started_ms": 32, "completed_ms": 35, "container_id": "new"},
            ],
            "deployment_evidence": {
                "old_container_id": "old", "new_container_id": "new",
                "container_events": [
                    {"event": "destroy", "container_id": "old", "at_ms": 20},
                    {"event": "create", "container_id": "new", "at_ms": 30},
                ],
                "connection_observer": {
                    "continuous": True, "socket_sample_count": 2,
                    "postgresql_session_sample_count": 2,
                    "application_container_id": "new", "started_ms": 25,
                    "completed_ms": 40,
                },
                "connection_attempt_log": [],
                "restart_trace_ids": ["restart-read"],
            },
        }

    def test_accepts_daemon_chronology_with_full_audit_coverage(self):
        validator.validate_observation_provenance(self.evidence())

    def test_rejects_reported_create_after_restart_probe(self):
        evidence = self.evidence()
        evidence["deployment_evidence"]["container_events"][1]["at_ms"] = 34
        with self.assertRaisesRegex(ValueError, "probe predates"):
            validator.validate_observation_provenance(evidence)

    def test_rejects_audit_started_after_create(self):
        evidence = self.evidence()
        evidence["deployment_evidence"]["connection_observer"]["started_ms"] = 31
        with self.assertRaisesRegex(ValueError, "started after"):
            validator.validate_observation_provenance(evidence)

    def test_rejects_audit_stopped_before_final_probe(self):
        evidence = self.evidence()
        evidence["deployment_evidence"]["connection_observer"]["completed_ms"] = 34
        with self.assertRaisesRegex(ValueError, "stopped before"):
            validator.validate_observation_provenance(evidence)

    def test_uses_docker_event_nanoseconds_not_local_clock(self):
        event = {"Action": "create", "timeNano": 1234567000000}
        completed = mock.Mock(returncode=0, stdout=json.dumps(event) + "\n", stderr="")
        with mock.patch.object(runner, "capture", return_value=completed):
            observed = runner.observed_container_event_ms("container", "create", 1, 2000)
        self.assertEqual(1234567, observed)


if __name__ == "__main__":
    unittest.main()
