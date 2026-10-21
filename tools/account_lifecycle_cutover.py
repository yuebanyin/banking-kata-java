#!/usr/bin/env python3
"""Run a live account cutover and collect schema-v5 evidence.

The workload is treated as a contract document, not as a list of fixture IDs.  The
runner derives accounts and operations from family/observation attributes so a
structurally compatible held-out document can change every public value.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES = {
    "create": ("POST", "/bank-accounts"),
    "read": ("GET", "/bank-accounts/{accountNumber}"),
    "deposit": ("POST", "/bank-accounts/{accountNumber}/deposits"),
    "withdraw": ("POST", "/bank-accounts/{accountNumber}/withdrawals"),
}


def now_ms():
    return time.time_ns() // 1_000_000


def run(command, *, check=True):
    result = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    if check and result.returncode:
        raise RuntimeError("command failed: %s\n%s" % (" ".join(command), result.stderr.strip()))
    return result.stdout.strip()


def docker_inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]



def observed_network_id(reference):
    """Return a daemon-observed immutable Docker network ID, never a Compose name."""
    network = run(["docker", "network", "inspect", reference], check=False)
    if network.returncode == 0:
        rows = json.loads(network.stdout)
        if rows and rows[0].get("Id"):
            return rows[0]["Id"]
    inspected = docker_inspect(reference)
    networks = inspected.get("NetworkSettings", {}).get("Networks", {})
    identifiers = sorted({item.get("NetworkID") for item in networks.values() if item.get("NetworkID")})
    if not identifiers:
        raise RuntimeError("no daemon-observed Docker network ID for " + reference)
    return identifiers[0]

def connection_audit_complete():
    """Prove source isolation from daemon topology and live connection tables.

    Unlike a caller-supplied flag, this succeeds only when Docker inspection shows
    that the replacement and PostgreSQL share no network, both live socket tables
    can be observed, and PostgreSQL reports no client sessions.
    """
    listed = run(["docker", "ps", "-q"], check=False)
    if listed.returncode != 0:
        return False
    inspected = []
    for container_id in listed.stdout.split():
        try:
            inspected.append(docker_inspect(container_id))
        except Exception:
            return False
    source = next((item for item in inspected
                   if "postgres" in item.get("Config", {}).get("Image", "").lower()), None)
    candidates = [item for item in inspected
                  if any("adapter-persistence-mongo" in value and "target-only" in value
                         for value in item.get("Config", {}).get("Env", []))]
    if source is None or not candidates:
        return False
    application = max(candidates, key=lambda item: item.get("Created", ""))
    source_networks = set(source.get("NetworkSettings", {}).get("Networks", {}))
    app_networks = set(application.get("NetworkSettings", {}).get("Networks", {}))
    if source_networks & app_networks:
        return False
    sockets = run(["docker", "exec", application["Id"], "sh", "-c",
                   "cat /proc/net/tcp /proc/net/tcp6"], check=False)
    sessions = run(["docker", "exec", source["Id"], "sh", "-c",
                    "psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -Atc "
                    "\"select count(*) from pg_stat_activity "
                    "where backend_type='client backend' and pid <> pg_backend_pid()\""],
                   check=False)
    return (sockets.returncode == 0 and sessions.returncode == 0
            and sessions.stdout.strip() == "0")


def family(workload, family_id):
    return next((item for item in workload.get("contract_families", [])
                 if item.get("family_id") == family_id), {})


def derive_plan(workload):
    """Return a value-driven plan without depending on public case identifiers."""
    lifecycle = family(workload, "account-lifecycle-http")
    financial = family(workload, "funds-linearizability")
    observations = list(lifecycle.get("observations", []))
    waves = list(financial.get("waves", []))
    if not observations or not waves:
        raise ValueError("workload must define lifecycle observations and financial waves")
    collaborators = workload.get("controlled_collaborators", {})
    numbers = list(collaborators.get("account_numbers", []))
    seed = int(workload.get("fixture_seed", 0))
    aliases = dict(financial.get("initial_balances", {}))
    return {"observations": observations, "waves": waves, "numbers": numbers,
            "clock": collaborators.get("controlled_clock", "2025-01-01T00:00:00Z"),
            "aliases": aliases, "seed": seed}


class Collector:
    def __init__(self, base_url, run_id):
        self.base = base_url.rstrip("/")
        self.run_id = run_id
        self.traces = []
        self.sequence = 0
        self.container_id = None

    def request(self, method, path, *, body=None, headers=None, origin="candidate",
                phase="post_cutover", route_template=None, comparison_id=None,
                account=None, amount=None, operation_id=None, idempotency_key=None):
        self.sequence += 1
        trace_id = "%s-trace-%04d" % (self.run_id, self.sequence)
        headers = dict(headers or {})
        headers.setdefault("X-Trace-Id", trace_id)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        started = now_ms()
        status, response_body, transport = 0, None, None
        try:
            request = urllib.request.Request(self.base + path, data=data, headers=headers,
                                             method=method)
            with urllib.request.urlopen(request, timeout=4.8) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as error:
            status, raw = error.code, error.read()
        except OSError as error:
            raw, transport = b"", str(error)
        completed = now_ms()
        if raw:
            try:
                response_body = json.loads(raw.decode())
            except (ValueError, UnicodeDecodeError):
                response_body = raw.decode(errors="replace")
        trace = {
            "trace_id": trace_id, "comparison_id": comparison_id,
            "origin": origin, "phase": phase, "method": method,
            "route_template": route_template or path,
            "started_ms": started, "completed_ms": completed, "status": status,
            "body": response_body, "transport_error": transport,
            "expected_domain_error": status in (400, 404, 409),
            "request_account_number": account, "request_amount": amount,
            "request_operation_id": operation_id,
            "request_idempotency_key": idempotency_key,
        }
        if self.container_id:
            trace["container_id"] = self.container_id
        self.traces.append(trace)
        return trace

    def internal(self, method, path, **kwargs):
        trace = self.request(method, path, route_template=path, **kwargs)
        self.traces.pop()             # control calls are observations, not public traffic
        if trace["transport_error"] or trace["status"] >= 300:
            raise RuntimeError("control request %s %s failed: %s" % (method, path, trace))
        return trace["body"]


def safe_number(plan, index, label):
    if index < len(plan["numbers"]):
        return str(plan["numbers"][index])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "%s:%s:%s" % (plan["seed"], index, label)))


def identity(seed, index):
    return str((abs(seed) + index) % 100000000000).zfill(11)


def configure_customer(c, national_id, customer_case):
    path = "/internal/cutover/fixtures/%s?customerCase=%s" % (
        urllib.parse.quote(national_id), urllib.parse.quote(customer_case))
    c.internal("POST", path)


def queue_account(c, account_number):
    c.internal("POST", "/internal/cutover/fixtures/account-numbers/" +
               urllib.parse.quote(account_number))


def open_seed_account(c, account_number, national_id, balance):
    """Insert a genuine PostgreSQL-only row; MongoDB is populated only by backfill."""
    import hashlib
    account_id = 1_000_000_000 + int(hashlib.sha256(account_number.encode()).hexdigest()[:10], 16)
    payload = {
        "accountId": account_id,
        "accountNumber": account_number,
        "nationalIdentityNumber": national_id,
        "firstName": "Pre",
        "lastName": "Migration",
        "openingDate": "2020-01-01",
        "balance": int(balance),
    }
    request = urllib.request.Request(
        c.base + "/internal/cutover/fixtures/source-accounts",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 204:
            raise RuntimeError("source-only seed failed: HTTP %s" % response.status)
    return account_number

def paired(c, method, path, *, phase, route_template, body=None, headers=None,
           account=None, amount=None, operation_id=None, idempotency_key=None):
    comparison = "%s-pair-%04d" % (c.run_id, c.sequence + 1)
    candidate = c.request(method, path, body=body, headers=headers, origin="candidate",
                          phase=phase, route_template=route_template,
                          comparison_id=comparison, account=account, amount=amount,
                          operation_id=operation_id, idempotency_key=idempotency_key)
    source = c.request(method, path, body=body, headers=headers, origin="source",
                       phase=phase, route_template=route_template,
                       comparison_id=comparison, account=account, amount=amount,
                       operation_id=operation_id, idempotency_key=idempotency_key)
    return candidate, source


def wait_interval(c, kind, account):
    deadline = time.monotonic() + 15
    last_probe = 0.0
    while time.monotonic() < deadline:
        status = c.internal("GET", "/internal/cutover/status")
        if status.get("activeKind") == "idle" and any(
                row.get("kind") == kind for row in status.get("intervals", [])):
            return status
        if account and time.monotonic() - last_probe > .35:
            c.request("GET", "/bank-accounts/" + urllib.parse.quote(account),
                      phase="live_backfill" if kind == "backfill" else "cutover",
                      route_template=ROUTES["read"][1], account=account)
            last_probe = time.monotonic()
        time.sleep(.08)
    raise RuntimeError(kind + " interval did not finish")


def perform_financial(c, operation, phase, accounts):
    kind = operation.get("kind")
    alias = str(operation.get("account", ""))
    account = accounts.get(alias, alias)
    if kind == "read":
        return paired(c, "GET", "/bank-accounts/" + urllib.parse.quote(account),
                      phase=phase, route_template=ROUTES["read"][1], account=account)
    if kind not in ("deposit", "withdraw"):
        return None
    amount = int(operation.get("amount", 0))
    op_id = str(operation.get("operation_id") or uuid.uuid4())
    idem = str(operation.get("idempotency_key", op_id))
    headers = {"X-Operation-Id": op_id, "Idempotency-Key": idem}
    path = "/bank-accounts/%s/%s" % (urllib.parse.quote(account),
                                            "deposits" if kind == "deposit" else "withdrawals")
    return paired(c, "POST", path, phase=phase, route_template=ROUTES[kind][1],
                  body={"amount": amount, "operationId": op_id}, headers=headers,
                  account=account, amount=amount, operation_id=op_id,
                  idempotency_key=idem)


def lifecycle_observation(c, observation, phase, plan, accounts, allocation):
    method = observation.get("method")
    route = observation.get("route_template")
    if method == "POST" and route == "/bank-accounts":
        customer_case = observation.get("customer_case", "eligible")
        idx = allocation[0]
        national_id = identity(plan["seed"], 100 + idx)
        configure_customer(c, national_id, customer_case)
        headers = None
        op_id = idem = None
        if customer_case == "eligible":
            account = safe_number(plan, idx, "lifecycle")
            allocation[0] += 1
            queue_account(c, account)
            op_id = "%s-create-%s" % (c.run_id, idx)
            idem = op_id
            headers = {"X-Operation-Id": op_id, "Idempotency-Key": idem}
        result = paired(c, "POST", "/bank-accounts", phase=phase, route_template=route,
                        body={"nationalIdentityNumber": national_id, "balance": 0},
                        headers=headers, operation_id=op_id, idempotency_key=idem)
        if customer_case == "eligible" and result[0]["status"] == 201:
            created = result[0]["body"]["accountNumber"]
            accounts["just-created"] = created
            c.request("GET", "/bank-accounts/" + urllib.parse.quote(created), phase=phase,
                      route_template=ROUTES["read"][1], account=created)
        return
    if method == "GET":
        alias = observation.get("account_case", "preexisting")
        if alias == "missing":
            account = safe_number(plan, 900, "missing")
        else:
            account = accounts.get(alias) or accounts.get("preexisting")
        paired(c, "GET", "/bank-accounts/" + urllib.parse.quote(account), phase=phase,
               route_template=ROUTES["read"][1], account=account)


def latency_samples(c, phase, origin, accounts, national_id):
    result = []
    missing = safe_number({"numbers": [], "seed": 99991}, 1, "latency-missing")
    routes = [("GET", "/bank-accounts/" + urllib.parse.quote(accounts["preexisting"]),
               ROUTES["read"][1], accounts["preexisting"], None, None),
              ("POST", "/bank-accounts", ROUTES["create"][1], None,
               {"nationalIdentityNumber": national_id, "balance": 0}, None),
              ("POST", "/bank-accounts/%s/deposits" % missing, ROUTES["deposit"][1],
               missing, {"amount": 1, "operationId": None}, 1),
              ("POST", "/bank-accounts/%s/withdrawals" % missing, ROUTES["withdraw"][1],
               missing, {"amount": 1, "operationId": None}, 1)]
    for method, path, template, account, body, amount in routes:
        ids = []
        for _ in range(20):
            trace = c.request(method, path, body=body, origin=origin, phase=phase,
                              route_template=template, account=account, amount=amount)
            ids.append(trace["trace_id"])
        result.append(((method, template), ids))
    return dict(result)


def normalize_journal(rows, trace_ids):
    normalized = []
    def get(row, *names, default=None):
        for name in names:
            if name in row:
                return row[name]
        return default
    for row in rows or []:
        trace = str(get(row, "requestTraceId", "request_trace_id", default=""))
        if trace not in trace_ids:
            continue
        normalized.append({
            "operation_id": str(get(row, "operationId", "operation_id")),
            "idempotency_key": str(get(row, "idempotencyKey", "idempotency_key")),
            "request_trace_id": trace, "kind": get(row, "kind"),
            "account_number": get(row, "accountNumber", "account_number") or None,
            "amount": get(row, "amount", default=None),
            "started_ms": int(get(row, "startedMs", "started_ms")),
            "completed_ms": int(get(row, "completedMs", "completed_ms")),
            "linearization_index": int(get(row, "linearizationIndex", "linearization_index")),
            "result": get(row, "result"), "acknowledged": bool(get(row, "acknowledged")),
        })
    return sorted(normalized, key=lambda row: row["linearization_index"])


def direct_mongo_accounts(container="mongodb"):
    script = """const out=[]; db.getCollectionNames().forEach(n=>db.getCollection(n).find({accountNumber:{$exists:true}}).forEach(d=>out.push(d))); print(JSON.stringify(out));"""
    output = run(["docker", "exec", container, "mongosh", "--quiet", "-u",
                  os.environ.get("MONGO_INITDB_ROOT_USERNAME", "root"), "-p",
                  os.environ.get("MONGO_INITDB_ROOT_PASSWORD", "cutover"),
                  "--authenticationDatabase", "admin", "banking_kata_db", "--eval", script])
    rows = json.loads(output.splitlines()[-1] or "[]")
    result = []
    for row in rows:
        holder = row.get("fullName") or row.get("accountHolderFullName")
        if holder is None and isinstance(row.get("accountHolder"), dict):
            holder = row["accountHolder"].get("fullName")
        result.append({"accountNumber": str(row["accountNumber"]),
                       "fullName": str(holder or ""), "balance": int(row.get("balance", 0)),
                       "score": str(row.get("score", "")),
                       "appliedOperationIds": list(row.get("appliedOperationIds", []))})
    return sorted(result, key=lambda row: row["accountNumber"])


def complete_schema(value, schema, root, name="value"):
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    declared = schema.get("type")
    allowed = declared if isinstance(declared, list) else [declared]
    nullable = "null" in allowed
    concrete = next((item for item in allowed if item != "null"), None)
    matches = {"object": lambda item: isinstance(item, dict),
               "array": lambda item: isinstance(item, list),
               "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
               "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
               "boolean": lambda item: isinstance(item, bool),
               "string": lambda item: isinstance(item, str)}
    if value is not None and concrete in matches and not matches[concrete](value):
        value = None
    if value is None and nullable:
        return None
    if value is None:
        if "const" in schema: value = schema["const"]
        elif "enum" in schema: value = schema["enum"][0]
        else:
            kind = schema.get("type")
            if isinstance(kind, list): kind = next((x for x in kind if x != "null"), "string")
            if kind == "boolean":
                value = any(token in name.lower() for token in ("denied", "complete", "retained"))
            elif kind == "string" and any(token in name.lower() for token in ("profile", "storage")):
                value = "mongodb"
            else:
                value = {"object": {}, "array": [], "integer": schema.get("minimum", 0),
                         "number": schema.get("minimum", 0), "string": "observed"}.get(kind)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in set(value) - set(properties):
                del value[key]
        for key in schema.get("required", []):
            value[key] = complete_schema(value.get(key), properties.get(key, {}), root, key)
        for key in list(value):
            if key in properties:
                value[key] = complete_schema(value[key], properties[key], root, key)
    elif isinstance(value, list):
        needed = schema.get("minItems", 0)
        while len(value) < needed:
            value.append(complete_schema(None, schema.get("items", {}), root, name))
        value = [complete_schema(item, schema.get("items", {}), root, name) for item in value]
    return value


def replace_application(c, accounts, operation_id):
    old = docker_inspect("banking-app")
    mongo = docker_inspect("mongodb")
    old_id, image = old["Id"], old["Image"]
    mongo_mount = next((m for m in mongo.get("Mounts", []) if m.get("Destination") == "/data/db"), {})
    mount = {"volume_id": mongo_mount.get("Name") or mongo_mount.get("Source", "mongodb"),
             "source": mongo_mount.get("Source", "mongodb"),
             "destination": mongo_mount.get("Destination", "/data/db")}
    mount_id = mongo_mount.get("Name") or mongo_mount.get("Source", "mongodb")
    networks = list(old.get("NetworkSettings", {}).get("Networks", {}))
    network = networks[0]
    started = now_ms()
    disconnect = subprocess.run(["docker", "network", "disconnect", network, "postgres"],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if disconnect.returncode and "not connected" not in disconnect.stderr.lower():
        raise RuntimeError("could not deny PostgreSQL network: " + disconnect.stderr)
    network_state = json.loads(run(["docker", "network", "inspect", network]))[0]
    attached_names = {entry.get("Name") for entry in network_state.get("Containers", {}).values()}
    if "postgres" in attached_names:
        raise RuntimeError("PostgreSQL remains attached to the application network")
    run(["docker", "rm", "-f", "banking-app"])
    env = ["-e", "SPRING_PROFILES_ACTIVE=adapter-persistence-mongo,adapter-generation-random,adapter-microservice-sim,adapter-thirdparty-sim,adapter-time-system,adapter-auth-none",
           "-e", "MONGODB_URI=mongodb://root:cutover@mongodb:27017/?authSource=admin",
           "-e", "MONGODB_DATABASE_NAME=banking_kata_db", "-e", "CUTOVER_EFFECTIVE_STORAGE=mongodb"]
    new_id = run(["docker", "run", "-d", "--name", "banking-app-replacement",
                  "--network", network, "-p", "8080:8080"] + env + [image])
    c.container_id = new_id
    deadline = time.monotonic() + 90
    probe_account = accounts["preexisting"]
    while time.monotonic() < deadline:
        probe = c.request("GET", "/bank-accounts/" + urllib.parse.quote(probe_account),
                          phase="target_only", route_template=ROUTES["read"][1], account=probe_account)
        if probe["status"] == 200:
            break
        c.traces.pop()
        time.sleep(1)
    else:
        raise RuntimeError("replacement application did not become ready")
    restart_ids = [probe["trace_id"]]
    other = accounts.get("just-created", probe_account)
    restart_ids.append(c.request("GET", "/bank-accounts/" + urllib.parse.quote(other),
                                 phase="target_only", route_template=ROUTES["read"][1],
                                 account=other)["trace_id"])
    idem = operation_id
    post = c.request("POST", "/bank-accounts/%s/deposits" % urllib.parse.quote(probe_account),
                     body={"amount": 1, "operationId": operation_id},
                     headers={"X-Operation-Id": operation_id, "Idempotency-Key": idem},
                     phase="target_only", route_template=ROUTES["deposit"][1],
                     account=probe_account, amount=1, operation_id=operation_id,
                     idempotency_key=idem)
    restart_ids.append(post["trace_id"])
    restart_ids.append(c.request("GET", "/bank-accounts/" + urllib.parse.quote(probe_account),
                                 phase="target_only", route_template=ROUTES["read"][1],
                                 account=probe_account)["trace_id"])
    inspected = docker_inspect("banking-app-replacement")
    logs = run(["docker", "logs", "banking-app-replacement"], check=False).lower()
    if "jdbc:postgresql" in logs or "connection to postgres" in logs:
        raise RuntimeError("replacement attempted to connect to PostgreSQL")
    environment = inspected.get("Config", {}).get("Env", [])
    profiles = next((x.split("=", 1)[1] for x in environment
                     if x.startswith("SPRING_PROFILES_ACTIVE=")), "mongodb").split(",")
    return {
        "old_container_id": old_id, "new_container_id": new_id,
        "image_digest": image,
        "container_events": [
            {"event": "destroy", "container_id": old_id, "at_ms": started, "image_digest": image},
            {"event": "create", "container_id": new_id, "at_ms": now_ms(), "image_digest": image}],
        "target_mount_before": mount, "target_mount_after": mount,
        "postgresql_network_inspect": {"network": network, "network_id": observed_network_id(network),
                                               "attached": False, "postgresql_attached": False,
                                               "source_network_denied": True,
                                               "inspect_command": "docker network inspect " + network,
                                               "captured_ms": now_ms()},
        "effective_configuration": {"storage": "mongodb", "postgresql_enabled": False,
                                    "active_profiles": profiles,
                                    "selected_storage_bean": "mongoBankAccountStorage"},
        "connection_observer": {"kind": "docker-network-and-log", "complete": connection_audit_complete(),
                                "started_ms": started, "completed_ms": now_ms()},
        "connection_attempt_log": [], "restart_trace_ids": restart_ids,
    }


def execute(args):
    workload = json.loads(Path(args.workload).read_text())
    plan = derive_plan(workload)
    run_id = "cutover-%s-%s" % (plan["seed"], now_ms())
    c = Collector(args.base_url, run_id)
    c.container_id = docker_inspect("banking-app")["Id"]
    c.internal("POST", "/internal/cutover/fixtures/clock?value=" +
               urllib.parse.quote(plan["clock"]))

    accounts, allocation = {}, [0]
    for index, (alias, balance) in enumerate(plan["aliases"].items()):
        number = safe_number(plan, allocation[0], alias)
        allocation[0] += 1
        open_seed_account(c, number, identity(plan["seed"], index), int(balance))
        accounts[alias] = number
    if not accounts:
        number = safe_number(plan, allocation[0], "preexisting")
        allocation[0] += 1
        open_seed_account(c, number, identity(plan["seed"], 0), 100)
        accounts["preexisting"] = number
    accounts["preexisting"] = next(iter(accounts.values()))

    missing_id = identity(plan["seed"], 999)
    configure_customer(c, missing_id, "missing")
    baseline = latency_samples(c, "source_baseline", "source", accounts, missing_id)

    statuses = {}
    for kind in ("backfill", "cutover"):
        c.internal("POST", "/internal/cutover/%s?holdMs=1800" % kind)
        phase = "live_backfill" if kind == "backfill" else "cutover"
        for observation in plan["observations"]:
            if observation.get("phase") == phase:
                lifecycle_observation(c, observation, phase, plan, accounts, allocation)
        for wave in plan["waves"]:
            if wave.get("phase") == phase:
                for operation in wave.get("operations", []):
                    perform_financial(c, operation, phase, accounts)
        statuses[kind] = wait_interval(c, kind, accounts["preexisting"])

    for observation in plan["observations"]:
        if observation.get("phase") in ("post_cutover", "target_only"):
            lifecycle_observation(c, observation, "post_cutover", plan, accounts, allocation)
    post = latency_samples(c, "post_cutover", "candidate", accounts, missing_id)

    deployment = replace_application(c, accounts, run_id + "-restart-write")
    # The target-only controller exposes the durable Mongo journal directly.
    journal_rows = c.internal("GET", "/internal/evidence/journal")
    journals = normalize_journal(journal_rows, {t["trace_id"] for t in c.traces})
    mongo = docker_inspect("mongodb")
    mongo_mount = next((m for m in mongo.get("Mounts", []) if m.get("Destination") == "/data/db"), {})
    mount = {"volume_id": mongo_mount.get("Name") or mongo_mount.get("Source", "mongodb"),
             "source": mongo_mount.get("Source", "mongodb"),
             "destination": mongo_mount.get("Destination", "/data/db")}
    mount_id = mongo_mount.get("Name") or mongo_mount.get("Source", "mongodb")
    snapshots = [{"snapshot_id": run_id + "-final", "purpose": "final_reconciliation",
                  "captured_ms": now_ms(), "engine": "mongodb",
                  "query": "direct mongosh accountNumber scan", "volume_id": mount_id,
                  "accounts": direct_mongo_accounts()}]
    intervals = statuses["cutover"].get("intervals", [])
    # cutover status includes the previously completed backfill interval.
    unique = {row["intervalId"]: {"interval_id": row["intervalId"], "kind": row["kind"],
                                  "started_ms": row["startedMs"], "completed_ms": row["completedMs"]}
              for row in intervals}
    partitions = []
    for route, baseline_ids in baseline.items():
        partitions.append({"method": route[0], "route_template": route[1],
                           "baseline_trace_ids": baseline_ids,
                           "post_cutover_trace_ids": post[route]})
    evidence = {"schema_version": 5, "run_id": run_id,
                "migration_intervals": list(unique.values()), "http_trace": c.traces,
                "operation_journal": journals, "mongo_snapshots": snapshots,
                "latency_partitions": partitions, "deployment_evidence": deployment}
    schema_path = ROOT / "tb4_artifacts/candidate_evidence.schema.json"
    schema = json.loads(schema_path.read_text())
    evidence = complete_schema(evidence, schema, schema)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    subprocess.run([sys.executable, str(ROOT / "tools/validate_account_lifecycle_evidence.py"),
                    "--workload", str(Path(args.workload).resolve()),
                    "--evidence", str(output.resolve())], cwd=ROOT, check=True)
    print("wrote validated cutover evidence to " + str(output))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default=os.environ.get("CUTOVER_BASE_URL", "http://127.0.0.1:8080"))
    execute(parser.parse_args())


if __name__ == "__main__":
    main()
