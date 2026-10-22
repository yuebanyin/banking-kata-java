#!/usr/bin/env python3
"""Generate deterministic, candidate-native live-cutover policy assets."""
from __future__ import annotations
import json, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
SEED = int(os.environ.get("TB4_ARTIFACT_SEED", "41031"))
if SEED != 41031:
    raise SystemExit("policy fixtures require TB4_ARTIFACT_SEED=41031")
def dump(name, value):
    (ROOT/name).write_text(json.dumps(value, indent=2, sort_keys=True)+"\n", encoding="utf-8")

routes = {
 "open": "POST /bank-accounts",
 "view": "GET /bank-accounts/{accountNumber}",
 "deposit": "POST /bank-accounts/{accountNumber}/deposits",
 "withdraw": "POST /bank-accounts/{accountNumber}/withdrawals",
}
contract = {
 "schema_version": 2, "fixture_seed": SEED,
 "backend_contract": {
  "source": {"engine":"postgresql","adapter":"jpa","profile":"jpa"},
  "target": {"engine":"mongodb","adapter":"spring-data-mongodb","profile":"mongodb"},
  "heterogeneous": True, "persist_target_volume_across_container_replacement": True,
  "source_network_denied_after_cutover": True, "fresh_container_required": True,
 },
 "limits": {"request_timeout_ms":5000,"post_cutover_p95_delta_ms":10,
  "latency_partition":"method-and-route-template","p95_method":"nearest-rank",
  "minimum_samples_per_partition_and_period":20,"maximum_traffic_gap_ms":1000,
  "unexpected_request_failures":0,"stale_or_inconsistent_reads":0},
 "invariants": [
  {"invariant_id":"MIG-1","preserved":True,
   "requirement":"Continuously invoke lifecycle and financial HTTP reads and writes while PostgreSQL-to-MongoDB backfill and cutover intervals are active.",
   "evidence":["timestamped migration intervals","linked HTTP trace","acknowledged-operation journal","direct MongoDB reconciliation"],
   "validation":"Both read and write request intervals overlap each active migration interval; traffic spans the complete migration with no gap above 1000 ms; every acknowledged write is bound to its external request and reconciled."},
  {"invariant_id":"MIG-2","preserved":True,
   "requirement":"Preserve complete source HTTP status/body behavior and linearizable, exactly-once balances without stale, lost, or duplicate effects.",
   "evidence":["paired source/candidate HTTP observations","acknowledged-operation journal","direct MongoDB snapshot"],
   "validation":"Compare recursively exact JSON bodies, replay held-out operations independently, enforce real-time order, and reconcile every acknowledged/HTTP-visible lifecycle document field and all resulting balances and idempotency keys."},
  {"invariant_id":"MIG-3","preserved":True,
   "requirement":"Keep every external request below 5000 ms and each method/route post-cutover nearest-rank p95 no more than 10 ms above its source p95.",
   "evidence":["HTTP trace IDs in route-specific baseline and post-cutover partitions"],
   "validation":"Recompute duration and p95 from at least 20 real trace rows for each of all four public routes, including deposit and withdrawal."},
  {"invariant_id":"MIG-4","preserved":True,
   "requirement":"Replace the application container, retain the MongoDB volume, deny PostgreSQL networking, and operate solely on MongoDB without source connection attempts.",
   "evidence":["container event stream","network inspection","effective profiles and bean report","connection-attempt log","restart HTTP probes","before/after direct snapshots"],
   "validation":"Require distinct old/new container IDs and destroy/create events, identical target volume identity, PostgreSQL deny evidence, MongoDB-only effective storage, complete observed connection interval with zero PostgreSQL attempts, and old/new state plus writes after replacement."},
 ],
 "required_evidence_files": {"candidate_output":"one JSON document conforming to candidate_evidence.schema.json"},
}

workloads = {
 "schema_version":2,"fixture_seed":SEED,
 "controlled_collaborators": {
  "account_numbers":["11111111-1111-4111-8111-111111111101","11111111-1111-4111-8111-111111111102","11111111-1111-4111-8111-111111111103"],
  "customer_cases":["eligible","missing","blacklisted"], "controlled_clock":"2025-02-03T10:15:30Z"},
 "financial_http_contract": {
  "requirement":"Add externally invocable deposit and withdrawal routes; internal use-case calls are not workload observations.",
  "routes":[routes["deposit"],routes["withdraw"]],
  "request_body":{"operationId":"string idempotency key","amount":"positive integer smallest currency units"},
  "success":{"status":204,"body":None},
  "errors":{"missing_account":{"status":404,"body":None},"invalid_amount":{"status":400,"body":None},"insufficient_funds":{"status":409,"body":None}},
 },
 "contract_families":[
  {"family_id":"account-lifecycle-http","primary_oracle":"exact paired HTTP parity and lifecycle-document reconciliation",
   "native_surface":[routes["open"],routes["view"]],
   "observations":[
    {"case_id":"eligible-create","customer_case":"eligible","phase":"live_backfill","method":"POST","route_template":"/bank-accounts"},
    {"case_id":"missing-customer-create","customer_case":"missing","phase":"live_backfill","method":"POST","route_template":"/bank-accounts"},
    {"case_id":"blacklisted-customer-create","customer_case":"blacklisted","phase":"cutover","method":"POST","route_template":"/bank-accounts"},
    {"case_id":"existing-account","account_case":"preexisting","phase":"cutover","method":"GET","route_template":"/bank-accounts/{accountNumber}"},
    {"case_id":"missing-account","account_case":"missing","phase":"target_only","method":"GET","route_template":"/bank-accounts/{accountNumber}"},
    {"case_id":"read-after-create","account_case":"just-created","phase":"target_only","method":"GET","route_template":"/bank-accounts/{accountNumber}"},
   ],
   "edge_cases":["source-captured missing-customer response","source-captured blacklisted-customer response","empty missing-account 404 body","read immediately after acknowledged create","duplicate backfill replay"]},
  {"family_id":"funds-linearizability","primary_oracle":"real-time-consistent replay plus exactly-once journal/snapshot reconciliation",
   "required_public_routes":[routes["deposit"],routes["withdraw"],routes["view"]],
   "initial_balances":{"hot-account":100,"exact-account":50,"unit-account":1},
   "waves":[
    {"barrier":"backfill-contention","phase":"live_backfill","operations":[
     {"operation_id":"fund-01","kind":"deposit","account":"hot-account","amount":25},
     {"operation_id":"fund-02","kind":"withdraw","account":"hot-account","amount":40},
     {"operation_id":"fund-03","kind":"read","account":"hot-account","after":["fund-01"]}]},
    {"barrier":"cutover-errors","phase":"cutover","operations":[
     {"operation_id":"fund-04","idempotency_key":"fund-01","kind":"deposit","account":"hot-account","amount":25},
     {"operation_id":"fund-05","kind":"withdraw","account":"exact-account","amount":50},
     {"operation_id":"fund-06","kind":"withdraw","account":"unit-account","amount":1},
     {"operation_id":"fund-07","kind":"withdraw","account":"hot-account","amount":500},
     {"operation_id":"fund-08","kind":"deposit","account":"absent-account","amount":4}]},
   ],
   "edge_cases":["same-account contention","duplicate operation ID","exact-balance withdrawal","smallest currency unit","rejected overdraft","missing account","causally later read"]},
  {"family_id":"durable-profile-cutover","primary_oracle":"raw replacement/configuration/network evidence plus target-only HTTP probes",
   "required_sequence":["persist target snapshot","deny PostgreSQL network","destroy old app container","create distinct app container","inspect effective profiles/bean","observe all connection attempts","read pre-cutover account","read post-cutover account","write and read after replacement"],
   "requirements":{"fresh_container_not_process_restart":True,"same_target_volume":True,"effective_profiles_exactly":["mongodb"],"source_attempts":0},
   "required_public_routes":[routes["open"],routes["view"],routes["deposit"],routes["withdraw"]]},
 ],
}

serialization = {
 "schema_version":1,
 "confirmed_native_sources":[
  "core/src/main/java/com/optivem/kata/banking/core/ports/driver/accounts/openaccount/OpenAccountResponse.java",
  "core/src/main/java/com/optivem/kata/banking/core/ports/driver/accounts/viewaccount/ViewAccountResponse.java",
  "adapter-restapi-spring/src/main/java/com/optivem/kata/banking/adapter/driver/restapi/spring/controllers/BankAccountController.java",
  "adapter-restapi-spring/src/test/java/com/optivem/kata/banking/adapter/driver/restapi/spring/BankingConsumerContractTest.java"],
 "responses": {
  "open_success":{"status":201,"body":{"accountNumber":"string"},"additional_properties":False},
  "view_success":{"status":200,"body":{"accountNumber":"string","fullName":"string","balance":"integer","score":"enum string"},"additional_properties":False},
  "view_missing":{"status":404,"body":None},
  "missing_customer":{"comparison":"capture complete native source HTTP status/body, then require exact candidate equality","required_observation":True},
  "blacklisted_customer":{"comparison":"capture complete native source HTTP status/body, then require exact candidate equality","required_observation":True},
 },
 "note":"Opening dates, customer IDs, and string balances are not fields in native lifecycle response DTOs."}

schema = {
 "$schema":"https://json-schema.org/draft/2020-12/schema","$id":"candidate_evidence.schema.json","title":"Banking cutover candidate evidence","type":"object","additionalProperties":False,
 "required":["schema_version","run_id","migration_intervals","http_trace","operation_journal","mongo_snapshots","latency_partitions","deployment_evidence"],
 "properties":{
  "schema_version":{"const":2},"run_id":{"type":"string","minLength":1},
  "migration_intervals":{"type":"array","minItems":2,"items":{"$ref":"#/$defs/interval"}},
  "http_trace":{"type":"array","minItems":1,"items":{"$ref":"#/$defs/http"}},
  "operation_journal":{"type":"array","minItems":1,"items":{"$ref":"#/$defs/journal"}},
  "mongo_snapshots":{"type":"array","minItems":2,"items":{"$ref":"#/$defs/snapshot"}},
  "latency_partitions":{"type":"array","minItems":4,"items":{"$ref":"#/$defs/partition"}},
  "deployment_evidence":{"$ref":"#/$defs/deployment"}},
 "$defs":{
  "interval":{"type":"object","additionalProperties":False,"required":["interval_id","kind","started_ms","completed_ms"],"properties":{"interval_id":{"type":"string"},"kind":{"enum":["backfill","cutover"]},"started_ms":{"type":"integer","minimum":0},"completed_ms":{"type":"integer","minimum":1}}},
  "http":{"type":"object","additionalProperties":False,"required":["trace_id","comparison_id","origin","phase","method","route_template","started_ms","completed_ms","status","body","transport_error","expected_domain_error"],"properties":{"container_id":{"type":"string"},"trace_id":{"type":"string"},"comparison_id":{"type":["string","null"]},"origin":{"enum":["source","candidate"]},"phase":{"enum":["source_baseline","live_backfill","cutover","target_only","target_only_restart"]},"method":{"enum":["GET","POST"]},"route_template":{"enum":["/bank-accounts","/bank-accounts/{accountNumber}","/bank-accounts/{accountNumber}/deposits","/bank-accounts/{accountNumber}/withdrawals"]},"started_ms":{"type":"integer","minimum":0},"completed_ms":{"type":"integer","minimum":1},"status":{"type":"integer","minimum":100,"maximum":599},"body":{},"transport_error":{"type":["string","null"]},"expected_domain_error":{"type":"boolean"}}},
  "journal":{"type":"object","additionalProperties":False,"required":["operation_id","idempotency_key","request_trace_id","kind","account_number","amount","started_ms","completed_ms","linearization_index","result","acknowledged"],"properties":{"operation_id":{"type":"string"},"idempotency_key":{"type":"string"},"request_trace_id":{"type":"string"},"kind":{"enum":["create","deposit","withdraw"]},"account_number":{"type":"string"},"amount":{"type":"integer","minimum":0},"started_ms":{"type":"integer"},"completed_ms":{"type":"integer"},"linearization_index":{"type":"integer","minimum":0},"result":{"enum":["applied","duplicate_ignored","account_not_found","invalid_amount","insufficient_funds"]},"acknowledged":{"type":"boolean"}}},
  "account":{"type":"object","additionalProperties":False,"required":["accountNumber","fullName","balance","score","appliedOperationIds"],"properties":{"accountNumber":{"type":"string"},"fullName":{"type":"string"},"balance":{"type":"integer"},"score":{"type":"string"},"appliedOperationIds":{"type":"array","items":{"type":"string"},"uniqueItems":True}}},
  "snapshot":{"type":"object","additionalProperties":False,"required":["snapshot_id","purpose","captured_ms","engine","query","volume_id","accounts"],"properties":{"snapshot_id":{"type":"string"},"purpose":{"enum":["pre_replacement","post_replacement","final_reconciliation"]},"captured_ms":{"type":"integer"},"engine":{"const":"mongodb"},"query":{"const":"direct_mongodb_query"},"volume_id":{"type":"string"},"accounts":{"type":"array","items":{"$ref":"#/$defs/account"}}}},
  "partition":{"type":"object","additionalProperties":False,"required":["method","route_template","baseline_trace_ids","post_cutover_trace_ids"],"properties":{"method":{"enum":["GET","POST"]},"route_template":{"type":"string"},"baseline_trace_ids":{"type":"array","minItems":20,"items":{"type":"string"},"uniqueItems":True},"post_cutover_trace_ids":{"type":"array","minItems":20,"items":{"type":"string"},"uniqueItems":True}}},
  "event":{"type":"object","additionalProperties":False,"required":["event","container_id","at_ms","image_digest"],"properties":{"event":{"enum":["destroy","create"]},"container_id":{"type":"string"},"at_ms":{"type":"integer"},"image_digest":{"type":"string"}}},
  "connection":{"type":"object","additionalProperties":False,"required":["at_ms","container_id","engine","destination","outcome"],"properties":{"at_ms":{"type":"integer"},"container_id":{"type":"string"},"engine":{"enum":["postgresql","mongodb","other"]},"destination":{"type":"string"},"outcome":{"enum":["connected","denied","failed"]}}},
  "deployment":{"type":"object","additionalProperties":False,"required":["old_container_id","new_container_id","image_digest","container_events","target_mount_before","target_mount_after","postgresql_network_inspect","effective_configuration","connection_observer","connection_attempt_log","restart_trace_ids"],"properties":{"old_container_id":{"type":"string"},"new_container_id":{"type":"string"},"image_digest":{"type":"string"},"container_events":{"type":"array","minItems":2,"items":{"$ref":"#/$defs/event"}},"target_mount_before":{"type":"object"},"target_mount_after":{"type":"object"},"postgresql_network_inspect":{"type":"object"},"effective_configuration":{"type":"object"},"connection_observer":{"type":"object"},"connection_attempt_log":{"type":"array","items":{"$ref":"#/$defs/connection"}},"restart_trace_ids":{"type":"array","minItems":4,"items":{"type":"string"},"uniqueItems":True}}}
 }
}

matrix = {"schema_version":2,"candidate_files":{
 "storage_port":"core/src/main/java/com/optivem/kata/banking/core/ports/driven/BankAccountStorage.java",
 "controller":"adapter-restapi-spring/src/main/java/com/optivem/kata/banking/adapter/driver/restapi/spring/controllers/BankAccountController.java",
 "open_response":"core/src/main/java/com/optivem/kata/banking/core/ports/driver/accounts/openaccount/OpenAccountResponse.java",
 "view_response":"core/src/main/java/com/optivem/kata/banking/core/ports/driver/accounts/viewaccount/ViewAccountResponse.java",
 "consumer_contract":"adapter-restapi-spring/src/test/java/com/optivem/kata/banking/adapter/driver/restapi/spring/BankingConsumerContractTest.java",
 "startup_config":"startup/src/main/resources/application.yml","compose_file":"docker-compose.yml"},
 "native_capabilities":["JPA/PostgreSQL source adapter","MongoDB target adapter","lifecycle REST controller","deposit and withdrawal use cases","Docker service topology"],
 "required_additions_not_present_at_baseline":["migration coordinator/backfill","dual-write or change capture","public deposit route","public withdrawal route","fresh-container cutover harness","structured evidence collector"]}

# Candidate evidence is self-contained: every journal row is bound to captured
# request values, and lifecycle documents are reconciled field-for-field.
request_fields = ["request_account_number", "request_amount", "request_operation_id", "request_idempotency_key"]
http_schema = schema["$defs"]["http"]
http_schema["required"].extend(request_fields)
http_schema["properties"].update({
 "request_account_number":{"type":["string","null"]},
 "request_amount":{"type":["integer","null"],"minimum":0},
 "request_operation_id":{"type":["string","null"]},
 "request_idempotency_key":{"type":["string","null"]},
})

# HTTP provenance has a closed, schema-backed vocabulary when present.
# Semantic validation requires these fields on runtime observations while the
# optional schema declaration preserves compatibility with pre-v5 fixtures.
http_schema["properties"].update({
 "collector_kind":{"enum":["public_route","jpa_source_observation","fixture_creation_truth"]},
 "actual_request_path":{"type":"string","minLength":1},
 "request_body":{"type":"object"},
 "controlled_creation_inputs":{
  "type":"object","additionalProperties":False,
  "required":["account_number","national_identity_number","initial_balance","controlled_clock"],
  "properties":{
   "account_number":{"type":"string","minLength":1},
   "national_identity_number":{"type":"string","minLength":1},
   "initial_balance":{"type":"integer","minimum":0},
   "controlled_clock":{"type":"string","minLength":1},
  },
 },
})
schema["properties"]["schema_version"]["const"] = 4
contract["evidence_binding_contract"] = {
 "journal_to_http":"Each acknowledged journal operation links to a candidate POST on the route for its kind. Captured operation/idempotency IDs match; financial path account and request amount also match. Financial status and body match the route/outcome contract and an exact source request pair. Every acknowledged 201 creation is unconditionally applied.",
 "all_request_success":"Every source and candidate trace has no transport error. Expected HTTP errors have exact source/candidate status and body parity; no unclassified 5xx is accepted.",
 "lifecycle_to_mongodb":"The final direct MongoDB snapshot contains every acknowledged creation and every account returned by a successful candidate GET. fullName, balance, and score equal the latest pre-snapshot GET, and appliedOperationIds exactly equal applied journal operation_id values. Every acknowledged 201 creation contributes its exact operation_id regardless of candidate classification."
}
workloads["candidate_evidence_bindings"] = {
 "required_http_request_fields":request_fields,
 "route_by_journal_kind":{"create":"POST /bank-accounts","deposit":"POST /bank-accounts/{accountNumber}/deposits","withdraw":"POST /bank-accounts/{accountNumber}/withdrawals"},
 "financial_request_match":["account_number","amount","operation_id","idempotency_key","route outcome status/body","source/candidate parity"],
 "create_match":["operation_id","idempotency_key","201 body.accountNumber","result=applied"],
 "all_request_failure_policy":["transport_error=null","expected errors paired exactly","unexpected 5xx rejected"],
 "final_lifecycle_match":["accountNumber","fullName","balance","score","applied journal operation_id values"]
}
for name,obj in (("migration_contract.json",contract),("public_workloads.json",workloads),("rest_serialization_contract.json",serialization),("candidate_evidence.schema.json",schema),("candidate_backend_matrix.json",matrix)):
    dump(name,obj)

# Schema-v5 cross-record requirements cannot be expressed by JSON Schema alone.
# Keep their public contract deterministic alongside the generated structural schema.
_accountability_marker="\n## Schema v5 bidirectional evidence accounting\n"
_readme_path=ROOT/"README.md"
_readme_base=_readme_path.read_text().split(_accountability_marker,1)[0].rstrip()+"\n"
_readme_path.write_text(_readme_base+_accountability_marker+"""

The independent verifier closes every evidence link in both directions:

- Every non-null `comparison_id` must identify exactly one source trace and one candidate trace. Their method, route, request account/amount, complete status, and complete body must agree. This applies to every captured comparison, not only the named mandatory lifecycle cases.
- Every successful candidate `POST` must carry operation and idempotency IDs and map to exactly one semantically matching journal entry. Every operation-bearing candidate `POST` (including expected domain failures) must also map exactly once; journals may not share request traces.
- Every direct MongoDB `appliedOperationIds` value must name an independently replayed, applied journal operation for that same account. The final list must be complete and ordered, with no orphan or duplicate snapshot operations.
- For every held-out financial account, the final direct MongoDB document must exactly match independent initial truth plus replay: account number, full name, score, final balance, and complete applied-operation ID list.

Duplicate/orphan trace IDs, comparison sides, journal links, account documents, and snapshot operation IDs are invalid evidence rather than ignorable telemetry.
""")

# Candidate evidence contract version 5 closes the version-4/version-5 split that
# would otherwise make policy-conforming evidence impossible to verify.
_evidence_schema_path=ROOT/"candidate_evidence.schema.json"
_evidence_schema=json.loads(_evidence_schema_path.read_text(encoding="utf-8"))
_evidence_schema["properties"]["schema_version"]["const"]=5
dump("candidate_evidence.schema.json",_evidence_schema)

_evidence_closure='''\n### Evidence schema v5 closure rules\n\nThe private runtime validates candidate evidence against `candidate_evidence.schema.json`\nbefore applying semantic checks. During every declared migration interval, overlap\ncounts include only successful GETs and acknowledged, journal-linked 2xx mutations;\nfailed POST attempts do not establish live write traffic. Every successful candidate\nGET must name its requested account and match independent replay at that request's\nobservation point. Replacement-container probes must include successful reads of an\nold account and a migrated account, one successful journaled write, and a successful\nread-after-write, all from the new container.\n'''
_readme_path=ROOT/"README.md"
_readme=_readme_path.read_text(encoding="utf-8")
if "### Evidence schema v5 closure rules" not in _readme:
    _readme_path.write_text(_readme+_evidence_closure,encoding="utf-8")
