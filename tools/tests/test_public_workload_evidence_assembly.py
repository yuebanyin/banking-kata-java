import importlib.util
import json
import tempfile
import types
import unittest
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
    def test_public_workload_passes_full_schema_before_semantic_validation(self):
        """Drive the real public plan through assembly and the real v5 schema gate."""
        schema = json.loads((ROOT / "tb4_artifacts/candidate_evidence.schema.json").read_text())
        properties = schema["properties"]
        http_schema = schema["$defs"]["http"]
        order = []
        intervals = [
            {"intervalId": "backfill-1", "kind": "backfill",
             "startedMs": 100, "completedMs": 200},
            {"intervalId": "cutover-1", "kind": "cutover",
             "startedMs": 300, "completedMs": 400},
        ]

        def latency_samples(collector, phase, origin, accounts, national_id):
            result = {}
            for method, route in runner.ROUTES.values():
                ids = []
                for index in range(20):
                    collector.sequence += 1
                    trace_id = "assembly-trace-%04d" % collector.sequence
                    trace = schema_specimen(http_schema, schema, trace_id)
                    trace.update({"trace_id": trace_id, "comparison_id": None,
                                  "origin": origin, "phase": phase, "method": method,
                                  "route_template": route, "started_ms": index * 2,
                                  "completed_ms": index * 2 + 1, "status": 404,
                                  "body": None, "transport_error": None,
                                  "expected_domain_error": True,
                                  "request_account_number": None,
                                  "request_amount": None,
                                  "request_operation_id": None,
                                  "request_idempotency_key": None})
                    collector.traces.append(trace)
                    ids.append(trace_id)
                result[(method, route)] = ids
            return result

        def inspect(reference):
            if reference == "banking-app":
                return {"Id": "old-app"}
            if reference == "mongodb":
                return {"Mounts": [{"Destination": "/data/db", "Name": "mongo-volume",
                                     "Source": "/var/lib/docker/volumes/mongo-volume"}]}
            raise AssertionError("unexpected inspection: " + reference)

        original_schema_validation = runner.assert_schema_complete

        def schema_validation(evidence, loaded_schema):
            result = original_schema_validation(evidence, loaded_schema)
            order.append("schema")
            return result

        def semantic_validator(command, **kwargs):
            self.assertIn("validate_account_lifecycle_evidence.py", " ".join(command))
            self.assertEqual(["schema"], order)
            order.append("semantic")
            return types.SimpleNamespace(returncode=0)

        deployment = schema_specimen(properties["deployment_evidence"], schema, "deployment")
        journal = schema_specimen(properties["operation_journal"], schema, "journal")
        snapshot_node = properties["mongo_snapshots"]
        if "$ref" in snapshot_node.get("items", {}):
            resolved_snapshot = schema_specimen(snapshot_node["items"], schema, "snapshot")
            snapshot_accounts = resolved_snapshot.get("accounts", [])
        else:
            snapshot_accounts = schema_specimen(
                snapshot_node["items"]["properties"]["accounts"], schema, "accounts")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            args = types.SimpleNamespace(
                workload=str(ROOT / "tb4_artifacts/public_workloads.json"),
                output=str(output), base_url="http://candidate")
            with mock.patch.object(runner, "Collector", AssemblyCollector), \
                    mock.patch.object(runner, "now_ms", return_value=500), \
                    mock.patch.object(runner, "docker_inspect", side_effect=inspect), \
                    mock.patch.object(runner, "open_seed_account"), \
                    mock.patch.object(runner, "lifecycle_observation"), \
                    mock.patch.object(runner, "perform_financial"), \
                    mock.patch.object(runner, "wait_interval",
                                      return_value={"intervals": intervals}), \
                    mock.patch.object(runner, "latency_samples", side_effect=latency_samples), \
                    mock.patch.object(runner, "replace_application", return_value=deployment), \
                    mock.patch.object(runner, "normalize_journal", return_value=journal), \
                    mock.patch.object(runner, "direct_mongo_accounts",
                                      return_value=snapshot_accounts), \
                    mock.patch.object(runner, "assert_latency_posts_journaled"), \
                    mock.patch.object(runner, "assert_schema_complete",
                                      side_effect=schema_validation), \
                    mock.patch.object(runner.subprocess, "run",
                                      side_effect=semantic_validator):
                runner.execute(args)

            evidence = json.loads(output.read_text())
            phases = {row["phase"] for row in evidence["http_trace"]}
            self.assertEqual(["schema", "semantic"], order)
            self.assertIn("target_only", phases)
            self.assertNotIn("post_cutover", phases)
            self.assertGreaterEqual(len(evidence["mongo_snapshots"]), 2)
            self.assertEqual(
                {"pre_replacement", "final_reconciliation"},
                {row["purpose"] for row in evidence["mongo_snapshots"]})
            self.assertTrue(all(row["query"] == "direct_mongodb_query"
                                for row in evidence["mongo_snapshots"]))


if __name__ == "__main__":
    unittest.main()
