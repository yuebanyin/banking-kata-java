#!/usr/bin/env python3
"""Validate policy fixtures and their candidate-native seams."""
from __future__ import annotations

import json
from pathlib import Path

ART = Path(__file__).resolve().parent
REPO = ART.parent


def load(name: str):
    return json.loads((ART / name).read_text(encoding="utf-8"))


contract = load("migration_contract.json")
workloads = load("public_workloads.json")
matrix = load("candidate_backend_matrix.json")
assert contract["schema_version"] == workloads["schema_version"] == matrix["schema_version"] == 1
assert contract["fixture_seed"] == workloads["fixture_seed"] == 41031
assert contract["backend_contract"]["heterogeneous"] is True
assert contract["backend_contract"]["source"]["engine"] != contract["backend_contract"]["target"]["engine"]
limits = contract["limits"]
assert limits == {
    "latency_partition": "method-and-route-template",
    "p95_method": "nearest-rank",
    "post_cutover_p95_delta_ms": 10,
    "request_timeout_ms": 5000,
    "stale_or_inconsistent_reads": 0,
    "unexpected_request_failures": 0,
}
invariants = contract["invariants"]
assert [row["invariant_id"] for row in invariants] == ["MIG-1", "MIG-2", "MIG-3", "MIG-4"]
assert all(row["preserved"] is True and row["evidence"] for row in invariants)
families = {row["family_id"]: row for row in workloads["contract_families"]}
assert set(families) == {"account-lifecycle-http", "funds-linearizability", "durable-profile-cutover"}
assert len({row["primary_oracle"] for row in families.values()}) == 3
funds = families["funds-linearizability"]
assert any(len(wave["operations"]) >= 2 for wave in funds["waves"])
assert {op["kind"] for wave in funds["waves"] for op in wave["operations"]} == {"deposit", "withdraw", "read"}
phases = set(contract["trace_schema"]["phases"])
assert {op["phase"] for op in families["account-lifecycle-http"]["operations"]} <= phases
assert {p["phase"] for p in families["durable-profile-cutover"]["probes"]} <= phases
assert "target_only_restart" in {p["phase"] for p in families["durable-profile-cutover"]["probes"]}

for relative in (matrix["candidate_files"]["storage_port"], matrix["candidate_files"]["startup_config"], matrix["candidate_files"]["compose_file"]):
    assert (REPO / relative).is_file(), relative
for filename in ("BankAccountController.java", "JpaBankAccountStorage.java", "MongoBankAccountStorage.java", "DepositFundsUseCase.java", "WithdrawFundsUseCase.java"):
    matches = list(REPO.rglob(filename))
    assert len(matches) == 1, (filename, matches)
storage = (REPO / matrix["candidate_files"]["storage_port"]).read_text(encoding="utf-8")
assert all(token in storage for token in ("find(", "add(", "update("))
controller = next(REPO.rglob("BankAccountController.java")).read_text(encoding="utf-8")
assert all(token in controller for token in ("PostMapping", "GetMapping", "bank-accounts"))
print("policy fixtures valid: 4 invariants, 3 distinct families, native seams resolved")
