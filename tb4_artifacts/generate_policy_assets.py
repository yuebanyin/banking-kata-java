#!/usr/bin/env python3
"""Generate fresh deterministic banking cutover policy fixtures."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = int(os.environ.get("TB4_ARTIFACT_SEED", "41031"))
if SEED != 41031:
    raise SystemExit("this fixture set is pinned to TB4_ARTIFACT_SEED=41031")


def write_json(name: str, value: object) -> None:
    (ROOT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


contract = {
    "schema_version": 1,
    "fixture_seed": SEED,
    "backend_contract": {
        "source": {"engine": "postgresql", "adapter": "jpa", "profile": "jpa"},
        "target": {"engine": "mongodb", "adapter": "spring-data-mongodb", "profile": "mongodb"},
        "heterogeneous": True,
        "source_unavailable_after_restart": True,
        "persist_target_volume_across_restart": True,
    },
    "limits": {
        "request_timeout_ms": 5000,
        "post_cutover_p95_delta_ms": 10,
        "latency_partition": "method-and-route-template",
        "p95_method": "nearest-rank",
        "unexpected_request_failures": 0,
        "stale_or_inconsistent_reads": 0,
    },
    "invariants": [
        {
            "invariant_id": "MIG-1",
            "preserved": True,
            "requirement": "Run source reads and writes before and during heterogeneous backfill and cutover without stopping customer traffic.",
            "evidence": ["phase-tagged request trace", "acknowledged-write reconciliation", "overlapping operation intervals"],
        },
        {
            "invariant_id": "MIG-2",
            "preserved": True,
            "requirement": "Match source status and complete parsed response body, with no unexpected error, stale read, lost update, or duplicate effect.",
            "evidence": ["source baseline observations", "candidate observations", "independent target state"],
        },
        {
            "invariant_id": "MIG-3",
            "preserved": True,
            "requirement": "Keep every request below 5000 ms and nearest-rank p95 after cutover no more than 10 ms above source baseline for each method-and-route template.",
            "evidence": ["per-request latency trace", "endpoint-partitioned baseline", "endpoint-partitioned post-cutover samples"],
        },
        {
            "invariant_id": "MIG-4",
            "preserved": True,
            "requirement": "Start a fresh application process with the source network-denied and read and write persisted target data without fallback or source attempts.",
            "evidence": ["source-denied restart probe", "old-and-new account reads", "source connection-attempt count"],
        },
    ],
    "trace_schema": {
        "required_request_fields": ["request_id", "phase", "method", "route_template", "started_ms", "completed_ms", "status", "body"],
        "phases": ["source_baseline", "live_backfill", "cutover", "target_only", "target_only_restart"],
        "exact_body_rule": "recursive JSON value equality with no ignored fields",
        "expected_domain_errors": "count as successful customer requests only when status and body exactly match the source baseline",
        "write_ack_rule": "a completed successful mutation must be present in independent target state and every causally later read",
    },
}

workloads = {
    "schema_version": 1,
    "fixture_seed": SEED,
    "controlled_collaborators": {
        "account_numbers": ["11111111-1111-4111-8111-111111111101", "11111111-1111-4111-8111-111111111102", "11111111-1111-4111-8111-111111111103"],
        "customer_ids": ["22222222-2222-4222-8222-222222222201", "22222222-2222-4222-8222-222222222202", "22222222-2222-4222-8222-222222222203"],
        "opening_dates": ["2025-02-03", "2025-02-04", "2025-02-05"],
        "customer_outcomes": ["eligible", "missing", "blacklisted"],
    },
    "contract_families": [
        {
            "family_id": "account-lifecycle-http",
            "primary_oracle": "exact status/body parity plus target reconciliation for account creation and lookup",
            "native_surface": ["POST /bank-accounts", "GET /bank-accounts/{accountNumber}"],
            "edge_cases": ["eligible customer", "missing customer", "blacklisted customer", "missing account", "read immediately after acknowledged create", "duplicate cutover replay"],
            "operations": [
                {"request_id": "life-01", "phase": "source_baseline", "method": "POST", "route_template": "/bank-accounts", "customer_case": "eligible"},
                {"request_id": "life-02", "phase": "live_backfill", "method": "GET", "route_template": "/bank-accounts/{accountNumber}", "account_case": "preexisting"},
                {"request_id": "life-03", "phase": "cutover", "method": "POST", "route_template": "/bank-accounts", "customer_case": "missing"},
                {"request_id": "life-04", "phase": "target_only", "method": "GET", "route_template": "/bank-accounts/{accountNumber}", "account_case": "just-created"},
            ],
        },
        {
            "family_id": "funds-linearizability",
            "primary_oracle": "controlled concurrent deposit/withdraw history is linearizable and has exactly-once target effects",
            "native_surface": ["DepositFundsUseCase.handle", "WithdrawFundsUseCase.handle", "BankAccountStorage.update", "GET /bank-accounts/{accountNumber}"],
            "initial_balances": {"hot-account": "100.00", "precision-account": "0.10"},
            "edge_cases": ["same-account contention", "exact-balance withdrawal", "rejected overdraft", "one-cent precision", "missing account", "duplicate operation identifier"],
            "waves": [
                {
                    "barrier": "hot-1",
                    "phase": "live_backfill",
                    "operations": [
                        {"operation_id": "fund-01", "kind": "deposit", "account": "hot-account", "amount": "25.00"},
                        {"operation_id": "fund-02", "kind": "withdraw", "account": "hot-account", "amount": "40.00"},
                        {"operation_id": "fund-03", "kind": "read", "account": "hot-account", "after": ["fund-01"]},
                    ],
                },
                {
                    "barrier": "hot-2",
                    "phase": "cutover",
                    "operations": [
                        {"operation_id": "fund-04", "kind": "withdraw", "account": "hot-account", "amount": "200.00"},
                        {"operation_id": "fund-05", "kind": "deposit", "account": "precision-account", "amount": "0.01"},
                    ],
                },
            ],
        },
        {
            "family_id": "durable-profile-cutover",
            "primary_oracle": "effective backend selection and target-only durability across a source-denied fresh application deployment",
            "native_surface": ["BankAccountStorage", "Spring profiles", "application.yml", "JPA and Mongo persistence adapters"],
            "edge_cases": ["both adapters discoverable", "source adapter remains primary", "partially copied account", "absent target update", "stale document", "source fallback on restart"],
            "probes": [
                {"probe_id": "deploy-01", "phase": "live_backfill", "action": "record_effective_storage_bean"},
                {"probe_id": "deploy-02", "phase": "target_only", "action": "deny_source_network"},
                {"probe_id": "deploy-03", "phase": "target_only_restart", "action": "start_fresh_application_process_with_persisted_target"},
                {"probe_id": "deploy-04", "phase": "target_only_restart", "action": "read_preexisting_and_post_cutover_accounts"},
                {"probe_id": "deploy-05", "phase": "target_only_restart", "action": "create_and_update_account"},
            ],
        },
    ],
}

matrix = {
    "schema_version": 1,
    "candidate_files": {
        "storage_port": "core/src/main/java/com/optivem/kata/banking/core/ports/driven/BankAccountStorage.java",
        "rest_controller_name": "BankAccountController.java",
        "source_adapter_name": "JpaBankAccountStorage.java",
        "target_adapter_name": "MongoBankAccountStorage.java",
        "startup_config": "startup/src/main/resources/application.yml",
        "compose_file": "docker-compose.yml",
    },
    "baseline_status": {
        "migration_coordinator_present": False,
        "dual_write_or_cdc_present": False,
        "atomic_financial_storage_contract_present": False,
        "target_only_profile_verified": False,
    },
    "collection_boundaries": [
        "HTTP status/body/latency trace partitioned by method and route template",
        "acknowledged mutation journal with stable operation identifiers",
        "independent MongoDB account snapshot",
        "effective Spring profile and selected BankAccountStorage bean",
        "source-denied fresh-process connection and request log",
    ],
}

readme = """# Banking live-cutover fixture baseline

These deterministic fixtures describe inputs and evidence boundaries; they do not implement a migration. The source is the repository's JPA/PostgreSQL account storage and the heterogeneous target is its MongoDB account storage.

All future contracts preserve four rules together: traffic overlaps backfill and cutover; status and complete JSON bodies match controlled source behavior with no stale/lost/duplicate effects; every request is below 5000 ms and each method-and-route p95 is at most 10 ms over its own baseline; and a fresh application process works from persisted target state while PostgreSQL is network-denied.

The fixtures intentionally expose three non-duplicate contract families:

- `account-lifecycle-http` is about account creation/lookup parity and copy reconciliation.
- `funds-linearizability` is about same-account financial contention and exactly-once effects; public GET observations and latency probes remain required.
- `durable-profile-cutover` is about effective adapter selection, source isolation, and deployment permanence; HTTP parity and latency probes remain required but are not its primary oracle.

Expected domain errors are valid only when their status and entire body equal the controlled source baseline. Transport errors, timeouts, unexpected 5xx responses, stale reads, and acknowledged writes absent from MongoDB are failures. Target persistence is retained across the fresh application restart; the target volume is not reset.

`generate_policy_assets.py` is byte-deterministic for seed 41031. `check_policy_assets.py` validates the fixture relationships and confirms that named native candidate seams still exist. No file in this directory is a migration implementation.
"""

write_json("migration_contract.json", contract)
write_json("public_workloads.json", workloads)
write_json("candidate_backend_matrix.json", matrix)
(ROOT / "README.md").write_text(readme, encoding="utf-8")
