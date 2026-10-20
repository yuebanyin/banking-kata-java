#!/usr/bin/env python3
"""Validate policy fixtures, schemas, family distinctness, and native REST truth."""
import json, re
from pathlib import Path
A=Path(__file__).resolve().parent; R=A.parent
load=lambda n: json.loads((A/n).read_text(encoding="utf-8"))
c=load("migration_contract.json"); w=load("public_workloads.json"); s=load("candidate_evidence.schema.json"); r=load("rest_serialization_contract.json"); m=load("candidate_backend_matrix.json")
assert c["schema_version"]==w["schema_version"]==m["schema_version"]==2
assert c["fixture_seed"]==w["fixture_seed"]==41031
assert c["backend_contract"]["source"]["engine"] != c["backend_contract"]["target"]["engine"]
assert c["backend_contract"]["fresh_container_required"] is True
assert c["limits"] == {"latency_partition":"method-and-route-template","maximum_traffic_gap_ms":1000,"minimum_samples_per_partition_and_period":20,"p95_method":"nearest-rank","post_cutover_p95_delta_ms":10,"request_timeout_ms":5000,"stale_or_inconsistent_reads":0,"unexpected_request_failures":0}
assert [x["invariant_id"] for x in c["invariants"]]==["MIG-1","MIG-2","MIG-3","MIG-4"]
assert all(x["preserved"] is True and x["evidence"] and x["validation"] for x in c["invariants"])
f={x["family_id"]:x for x in w["contract_families"]}
assert set(f)=={"account-lifecycle-http","funds-linearizability","durable-profile-cutover"}
assert len({x["primary_oracle"] for x in f.values()})==3
life=f["account-lifecycle-http"]["observations"]
assert {x.get("customer_case") for x in life} >= {"eligible","missing","blacklisted"}
assert any(x.get("account_case")=="missing" for x in life)
funds=f["funds-linearizability"]
assert {"POST /bank-accounts/{accountNumber}/deposits","POST /bank-accounts/{accountNumber}/withdrawals"} <= set(funds["required_public_routes"])
assert {o["kind"] for wave in funds["waves"] for o in wave["operations"]}=={"deposit","withdraw","read"}
assert s["type"]=="object" and s["additionalProperties"] is False
assert set(s["required"])=={"schema_version","run_id","migration_intervals","http_trace","operation_journal","mongo_snapshots","latency_partitions","deployment_evidence"}
assert s["$defs"]["partition"]["properties"]["baseline_trace_ids"]["minItems"]==20
assert r["responses"]["open_success"]["body"]=={"accountNumber":"string"}
assert r["responses"]["view_success"]["body"]=={"accountNumber":"string","fullName":"string","balance":"integer","score":"enum string"}
assert r["responses"]["view_missing"]["body"] is None
for rel in m["candidate_files"].values(): assert (R/rel).is_file(), rel
open_dto=(R/m["candidate_files"]["open_response"]).read_text()
view_dto=(R/m["candidate_files"]["view_response"]).read_text()
controller=(R/m["candidate_files"]["controller"]).read_text()
pact=(R/m["candidate_files"]["consumer_contract"]).read_text()
# Focused native serialization validation: Lombok/Jackson property declarations and controller statuses.
assert re.findall(r"private\s+\w+\s+(\w+);", open_dto)==["accountNumber"]
assert re.findall(r"private\s+\w+\s+(\w+);", view_dto)==["accountNumber","fullName","balance","score"]
assert "private int balance;" in view_dto and "private Score score;" in view_dto
assert "HttpStatus.CREATED" in controller and "HttpStatus.OK" in controller and "HttpStatus.NOT_FOUND" in controller
assert "new ResponseEntity<>(HttpStatus.NOT_FOUND)" in controller
assert "status(HttpStatusValue.NOT_FOUND)" in pact and "ACCOUNT_NUMBER_FIELD" in pact and "FULL_NAME_FIELD" in pact
text=(A/"SOURCE_PRESERVATION.md").read_text()
assert text.count("MIG-1 (`preserved=true`)")==1 and text.count("MIG-2 (`preserved=true`)")==1
assert text.count("MIG-3 (`preserved=true`)")==1 and text.count("MIG-4 (`preserved=true`)")==1
assert "contextual_constraint_changes: []" in text
print("policy assets valid: native lifecycle serialization, evidence schema, 4 preserved invariants, 3 distinct families")
