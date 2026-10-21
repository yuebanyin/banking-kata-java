#!/usr/bin/env python3
"""Run a live account cutover and collect schema-v5 evidence.

The workload is treated as a contract document, not as a list of fixture IDs.  The
runner derives accounts and operations from family/observation attributes so a
structurally compatible held-out document can change every public value.
"""
import argparse
import hashlib
import re
import json
import os
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
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


def capture(command):
    return subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)


def run(command, *, check=True):
    result = capture(command)
    if check and result.returncode:
        raise RuntimeError("command failed: %s\n%s" % (" ".join(command), result.stderr.strip()))
    return result.stdout.strip()


def docker_inspect(reference):
    rows = json.loads(run(["docker", "inspect", reference]))
    if len(rows) != 1:
        raise RuntimeError("expected exactly one inspected object: " + reference)
    return rows[0]


def observed_network_id(reference):
    """Return a daemon-observed immutable Docker network ID, never a Compose name."""
    result = capture(["docker", "network", "inspect", reference])
    if result.returncode == 0:
        rows = json.loads(result.stdout)
        if len(rows) == 1 and rows[0].get("Id"):
            return rows[0]["Id"]
    inspected = docker_inspect(reference)
    identifiers = sorted({item.get("NetworkID") for item in
                          inspected.get("NetworkSettings", {}).get("Networks", {}).values()
                          if item.get("NetworkID")})
    if len(identifiers) != 1:
        raise RuntimeError("expected one daemon-observed network ID for " + reference)
    return identifiers[0]


def replacement_environment():
    return [
        "SPRING_PROFILES_ACTIVE=adapter-persistence-mongo,target-only,adapter-generation-random,adapter-microservice-sim,adapter-thirdparty-sim,adapter-time-system,adapter-auth-none",
        "MONGODB_URI=mongodb://root:cutover@mongodb:27017/?authSource=admin",
        "MONGODB_DATABASE_NAME=banking_kata_db",
        "CUTOVER_EFFECTIVE_STORAGE=mongodb",
    ]


def _environment_map(environment):
    return {entry.split("=", 1)[0]: entry.split("=", 1)[1]
            for entry in environment if isinstance(entry, str) and "=" in entry}


def is_target_only_mongo_environment(environment):
    variables = _environment_map(environment)
    profiles = {item.strip() for item in variables.get("SPRING_PROFILES_ACTIVE", "").split(",")
                if item.strip()}
    return ("adapter-persistence-mongo" in profiles and "target-only" in profiles and
            "adapter-persistence-jpa" not in profiles and
            variables.get("CUTOVER_EFFECTIVE_STORAGE") == "mongodb")


def _compose_labels(inspected):
    return inspected.get("Config", {}).get("Labels", {}) or {}


def scoped_compose_container(service, project):
    """Resolve a service through this Compose project and verify daemon labels."""
    ids = [item for item in run(["docker-compose", "ps", "-q", service]).splitlines() if item]
    if len(ids) != 1:
        raise RuntimeError("expected one %s container in Compose project" % service)
    inspected = docker_inspect(ids[0])
    labels = _compose_labels(inspected)
    if labels.get("com.docker.compose.service") != service:
        raise RuntimeError("container is not scoped to Compose service " + service)
    if project and labels.get("com.docker.compose.project") != project:
        raise RuntimeError("container belongs to another Compose project")
    return inspected


def scoped_common_network(application, source, mongo, project):
    network_sets = []
    for item in (application, source, mongo):
        network_sets.append(set(item.get("NetworkSettings", {}).get("Networks", {})))
    candidates = set.intersection(*network_sets)
    scoped = []
    for name in candidates:
        inspected = json.loads(run(["docker", "network", "inspect", name]))[0]
        labels = inspected.get("Labels", {}) or {}
        if not project or labels.get("com.docker.compose.project") == project:
            scoped.append((name, inspected["Id"]))
    if len(scoped) != 1:
        raise RuntimeError("expected one project-scoped application/source/MongoDB network")
    return scoped[0]


def _network_ids(inspected):
    return {value.get("NetworkID") for value in
            inspected.get("NetworkSettings", {}).get("Networks", {}).values()
            if value.get("NetworkID")}


def _container_ips(inspected):
    return {value.get("IPAddress") for value in
            inspected.get("NetworkSettings", {}).get("Networks", {}).values()
            if value.get("IPAddress")}


def _postgres_established_sockets(proc_net):
    matches = []
    for line in proc_net.splitlines():
        fields = line.split()
        if len(fields) < 4 or ":" not in fields[2]:
            continue
        try:
            remote_port = int(fields[2].rsplit(":", 1)[1], 16)
        except ValueError:
            continue
        if remote_port == 5432 and fields[3] == "01":
            matches.append(line.strip())
    return matches


def assess_source_isolation(application, source, denied_network_id, proc_net,
                            pg_sessions, logs, effective_configuration, captured_ms,
                            observer_started_ms=None, observer_completed_ms=None):
    """Derive the audit solely from scoped daemon/runtime observations."""
    app_networks = _network_ids(application)
    source_networks = _network_ids(source)
    established = _postgres_established_sockets(proc_net)
    app_ips = _container_ips(application)
    session_rows = [line.split("|", 2) for line in pg_sessions.splitlines() if line.strip()]
    replacement_sessions = [row for row in session_rows if row and row[0] in app_ips]
    relevant_logs = [line.strip() for line in logs.splitlines()
                     if re.search(r"postgres|jdbc:postgresql", line, re.IGNORECASE)]
    attempts = []
    for line in relevant_logs:
        failed = bool(re.search(r"denied|refused|unknown host|timed out|failed", line, re.IGNORECASE))
        attempts.append({"at_ms": captured_ms, "container_id": application["Id"],
                         "destination": "application-log:" + hashlib.sha256(line.encode()).hexdigest()[:16],
                         "engine": "postgresql", "outcome": "failed" if failed else "connected"})
    observed_environment = application.get("Config", {}).get("Env", [])
    environment_ok = is_target_only_mongo_environment(observed_environment)
    config_ok = (effective_configuration.get("storage") == "mongodb" and
                 not effective_configuration.get("postgresql_enabled", True) and
                 "mongo" in effective_configuration.get("selected_storage_bean", "").lower())
    complete = (denied_network_id in app_networks and denied_network_id not in source_networks and
                not (app_networks & source_networks) and not established and
                not replacement_sessions and environment_ok and config_ok and not relevant_logs)
    observer = {
        "kind": "scoped-docker-network-proc-socket-pg-stat-activity",
        "complete": complete,
        "started_ms": observer_started_ms if observer_started_ms is not None else captured_ms,
        "completed_ms": observer_completed_ms if observer_completed_ms is not None else now_ms(),
        "application_container_id": application["Id"],
        "source_container_id": source["Id"],
        "observed_network_ids": sorted(app_networks),
        "source_network_ids": sorted(source_networks),
        "proc_net_sha256": hashlib.sha256(proc_net.encode()).hexdigest(),
        "postgres_established_socket_count": len(established),
        "pg_stat_activity_sha256": hashlib.sha256(pg_sessions.encode()).hexdigest(),
        "replacement_postgresql_session_count": len(replacement_sessions),
        "application_log_sha256": hashlib.sha256(logs.encode()).hexdigest(),
        "postgres_log_line_count": len(relevant_logs),
    }
    return observer, attempts


def _rfc3339(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def collect_source_isolation(application_id, source_id, denied_network_id, effective_configuration,
                             observer_started_ms=None, proc_samples=None, session_samples=None,
                             observer_completed_ms=None):
    captured_ms = now_ms()
    socket_result = capture(["docker", "exec", application_id, "sh", "-c",
                             "cat /proc/net/tcp /proc/net/tcp6"])
    if socket_result.returncode:
        raise RuntimeError("could not observe replacement socket tables: " + socket_result.stderr.strip())
    session_result = capture(["docker", "exec", source_id, "sh", "-c",
                              "psql -U \"$POSTGRES_USER\" -d banking_kata_db -At -F '|' -c "
                              "\"select coalesce(client_addr::text,''),coalesce(application_name,''),state "
                              "from pg_stat_activity where backend_type='client backend'\""])
    if session_result.returncode:
        raise RuntimeError("could not observe PostgreSQL sessions: " + session_result.stderr.strip())
    since = _rfc3339(observer_started_ms or 0)
    log_result = capture(["docker", "logs", "--since", since, application_id])
    if log_result.returncode:
        raise RuntimeError("could not observe replacement logs: " + log_result.stderr.strip())
    proc_rows = list(proc_samples or []) + [socket_result.stdout]
    session_rows = list(session_samples or []) + [session_result.stdout]
    completed_ms = observer_completed_ms or now_ms()
    observer, attempts = assess_source_isolation(
        docker_inspect(application_id), docker_inspect(source_id), denied_network_id,
        "\n".join(proc_rows), "\n".join(session_rows),
        log_result.stdout + log_result.stderr, effective_configuration, captured_ms,
        observer_started_ms=observer_started_ms,
        observer_completed_ms=completed_ms)
    observer["socket_sample_count"] = len(proc_rows)
    observer["postgresql_session_sample_count"] = len(session_rows)
    observer["continuous"] = bool(observer_started_ms is not None and proc_samples and session_samples)
    if observer["continuous"]:
        observer["kind"] = "continuous-docker-network-proc-socket-pg-stat-activity"
    observer["complete"] = bool(observer["complete"] and observer["continuous"])
    return observer, attempts


class ContinuousSourceAudit:
    """Continuously sample source sessions and replacement sockets during MIG-4.

    Docker logs are read for this same window at finish.  Starting this object before
    ``docker run`` and stopping it after the final HTTP probe closes the transient
    startup-attempt hole left by a point-in-time audit.
    """
    def __init__(self, source_id, interval_seconds=.10):
        self.source_id = source_id
        self.interval_seconds = interval_seconds
        self.started_ms = now_ms()
        self.application_id = None
        self.proc_samples = []
        self.session_samples = []
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._sample, name="cutover-source-audit", daemon=True)
        self._thread.start()

    def set_application(self, application_id):
        self.application_id = application_id

    def _sample(self):
        session_command = ["docker", "exec", self.source_id, "sh", "-c",
                           "psql -U \"$POSTGRES_USER\" -d banking_kata_db -At -F '|' -c "
                           "\"select coalesce(client_addr::text,''),coalesce(application_name,''),state "
                           "from pg_stat_activity where backend_type='client backend'\""]
        while not self._stop.is_set():
            sessions = capture(session_command)
            app_id = self.application_id
            sockets = None
            if app_id:
                sockets = capture(["docker", "exec", app_id, "sh", "-c",
                                   "cat /proc/net/tcp /proc/net/tcp6"])
            with self._lock:
                if sessions.returncode == 0:
                    self.session_samples.append(sessions.stdout)
                if sockets is not None and sockets.returncode == 0:
                    self.proc_samples.append(sockets.stdout)
            self._stop.wait(self.interval_seconds)

    def finish(self, denied_network_id, effective_configuration):
        completed_ms = now_ms()
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("continuous source audit did not stop")
        if not self.application_id or not self.proc_samples or not self.session_samples:
            raise RuntimeError("continuous source audit did not capture the replacement window")
        return collect_source_isolation(
            self.application_id, self.source_id, denied_network_id, effective_configuration,
            observer_started_ms=self.started_ms, proc_samples=self.proc_samples,
            session_samples=self.session_samples, observer_completed_ms=completed_ms)


def observed_container_event_ms(container_id, action, since_ms, until_ms):
    """Read an actual daemon event timestamp; caller-supplied wall clock is not evidence."""
    result = capture(["docker", "events", "--since", _rfc3339(since_ms - 1000),
                      "--until", _rfc3339(until_ms + 1), "--filter", "container=" + container_id,
                      "--filter", "event=" + action, "--format", "{{json .}}"])
    if result.returncode:
        raise RuntimeError("could not read Docker %s event: %s" % (action, result.stderr.strip()))
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    matching = [row for row in rows if row.get("Action", row.get("status")) == action]
    if len(matching) != 1:
        raise RuntimeError("expected one Docker %s event for %s" % (action, container_id))
    event = matching[0]
    if event.get("timeNano") is not None:
        return int(event["timeNano"]) // 1_000_000
    if event.get("time") is not None:
        return int(event["time"]) * 1000
    raise RuntimeError("Docker event omitted its daemon timestamp")

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
    """Collect public candidate results and independent PostgreSQL source receipts.

    Candidate requests use the public route.  Their source partner is obtained through
    a read-only evidence endpoint which talks directly to JpaBankAccountStorage.  In
    particular, a mutating request is never issued a second time merely to create its
    source trace.
    """
    def __init__(self, base_url, run_id, evidence_time=None):
        self.base = base_url.rstrip("/")
        self.run_id = run_id
        self.evidence_time = evidence_time or "2025-01-01T00:00:00Z"
        self.traces = []
        self.sequence = 0
        self.container_id = None

    def _exchange(self, method, path, body, headers):
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
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
        if raw:
            try:
                response_body = json.loads(raw.decode())
            except (ValueError, UnicodeDecodeError):
                response_body = raw.decode(errors="replace")
        return status, response_body, transport

    def _record(self, *, trace_id, comparison_id, origin, phase, method,
                route_template, started, completed, status, response_body,
                transport, account, amount, operation_id, idempotency_key):
        trace = {
            "trace_id": trace_id, "comparison_id": comparison_id,
            "origin": origin, "phase": phase, "method": method,
            "route_template": route_template,
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

    def request(self, method, path, *, body=None, headers=None, origin="candidate",
                phase="post_cutover", route_template=None, comparison_id=None,
                account=None, amount=None, operation_id=None, idempotency_key=None):
        headers = dict(headers or {})
        if origin == "source":
            headers["X-TB4-Source-Only"] = "true"
        if origin == "source":
            comparison = comparison_id or "%s-pair-%04d" % (self.run_id, self.sequence + 1)
            candidate = self.request(
                method, path, body=body, headers=headers, origin="candidate", phase=phase,
                route_template=route_template, comparison_id=comparison, account=account,
                amount=amount, operation_id=operation_id, idempotency_key=idempotency_key)
            return self.source_observation(
                candidate, method, path, phase=phase,
                route_template=route_template or path, comparison_id=comparison,
                account=account, amount=amount, operation_id=operation_id,
                idempotency_key=idempotency_key, request_body=body)
        self.sequence += 1
        trace_id = "%s-trace-%04d" % (self.run_id, self.sequence)
        headers = dict(headers or {})
        headers.setdefault("X-Trace-Id", trace_id)
        headers.setdefault("X-Evidence-Time", self.evidence_time)
        started = now_ms()
        status, response_body, transport = self._exchange(method, path, body, headers)
        completed = now_ms()
        return self._record(
            trace_id=trace_id, comparison_id=comparison_id, origin=origin, phase=phase,
            method=method, route_template=route_template or path, started=started,
            completed=completed, status=status, response_body=response_body,
            transport=transport, account=account, amount=amount,
            operation_id=operation_id, idempotency_key=idempotency_key)

    def source_observation(self, candidate, method, path, *, phase, route_template,
                           comparison_id, account=None, amount=None, operation_id=None,
                           idempotency_key=None, request_body=None):
        """Read a source receipt without replaying the public operation."""
        self.sequence += 1
        trace_id = "%s-trace-%04d" % (self.run_id, self.sequence)
        payload = {
            "method": method, "path": path, "routeTemplate": route_template,
            "requestAccountNumber": account, "requestAmount": amount,
            "requestOperationId": operation_id,
            "requestIdempotencyKey": idempotency_key,
            "requestBody": request_body,
            "responseTimestamp": self.evidence_time,
        }
        observation_started = now_ms()
        control_status, receipt, transport = self._exchange(
            "POST", "/internal/evidence/source-observation", payload,
            {"X-Trace-Id": trace_id})
        observation_completed = now_ms()
        if transport or control_status != 200 or not isinstance(receipt, dict):
            raise RuntimeError("PostgreSQL source observation failed: %s %s %s" %
                               (control_status, transport, receipt))
        if "status" not in receipt or "body" not in receipt:
            raise RuntimeError("PostgreSQL source receipt was incomplete")
        # A source trace is a receipt recorded after the public source-backed request,
        # not a second execution of that request.  Anchor it when the real receipt call
        # completes, while retaining the measured public-route duration used by the
        # source latency partition.
        public_duration = candidate["completed_ms"] - candidate["started_ms"]
        receipt_started = max(observation_completed, candidate["completed_ms"])
        return self._record(
            trace_id=trace_id, comparison_id=comparison_id, origin="source", phase=phase,
            method=method, route_template=route_template,
            started=receipt_started, completed=receipt_started + public_duration,
            status=receipt["status"], response_body=receipt["body"],
            transport=None, account=account, amount=amount, operation_id=operation_id,
            idempotency_key=idempotency_key)

    def internal(self, method, path, **kwargs):
        trace = self.request(method, path, route_template=path, **kwargs)
        self.traces.pop()
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
    source = c.source_observation(candidate, method, path, phase=phase,
                                  route_template=route_template,
                                  comparison_id=comparison, account=account, amount=amount,
                                  operation_id=operation_id, idempotency_key=idempotency_key,
                                  request_body=body)
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
        for sample_index in range(20):
            headers = None
            operation_id = None
            idempotency_key = None
            # The repeatable latency POSTs deliberately exercise deterministic
            # domain-error paths (missing customer/account).  They are not acknowledged
            # operations and therefore must not manufacture operation identities.  If a
            # changed workload/configuration unexpectedly makes one successful, the
            # post-collection journal assertion below rejects the run.
            trace = c.request(
                method, path, body=body, headers=headers, origin=origin, phase=phase,
                route_template=template, account=account, amount=amount,
                operation_id=operation_id, idempotency_key=idempotency_key)
            ids.append(trace["trace_id"])
        result.append(((method, template), ids))
    return dict(result)


def assert_latency_posts_journaled(traces, journals, partitions):
    """Require one durable journal row for every successful latency POST execution."""
    by_id = {trace["trace_id"]: trace for trace in traces}
    by_comparison = {}
    for trace in traces:
        comparison = trace.get("comparison_id")
        if comparison is not None:
            by_comparison.setdefault(comparison, []).append(trace)
    rows_by_trace = {}
    for row in journals:
        rows_by_trace.setdefault(row["request_trace_id"], []).append(row)

    latency_ids = set()
    for partition in partitions:
        latency_ids.update(partition["baseline_trace_ids"])
        latency_ids.update(partition["post_cutover_trace_ids"])
    for trace_id in latency_ids:
        trace = by_id[trace_id]
        execution = trace
        if trace["origin"] == "source":
            candidates = [row for row in by_comparison.get(trace["comparison_id"], [])
                          if row["origin"] == "candidate"]
            if len(candidates) != 1:
                raise ValueError("source latency trace has no unique public execution: " + trace_id)
            execution = candidates[0]
        if execution["method"] != "POST" or not 200 <= execution["status"] < 300:
            continue
        linked = rows_by_trace.get(execution["trace_id"], [])
        if len(linked) != 1:
            raise ValueError("successful latency POST must have exactly one journal: " +
                             execution["trace_id"])
        row = linked[0]
        if (not execution.get("request_operation_id") or
                row.get("operation_id") != execution["request_operation_id"] or
                row.get("idempotency_key") != execution.get("request_idempotency_key")):
            raise ValueError("successful latency POST journal identity mismatch: " +
                             execution["trace_id"])


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


def assert_schema_complete(value, schema):
    """Reject absent or malformed observations without manufacturing evidence."""
    try:
        import jsonschema
    except ImportError as exc:
        raise RuntimeError("jsonschema is required to validate collected evidence") from exc
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(value),
                    key=lambda error: list(error.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(item) for item in error.absolute_path) or "$"
        raise ValueError("incomplete observed evidence at %s: %s" %
                         (location, error.message))
    return value


def replace_application(c, accounts, operation_id):
    old = docker_inspect("banking-app")
    project = _compose_labels(old).get("com.docker.compose.project")
    if not project:
        raise RuntimeError("banking-app is not a scoped Compose container")
    source_before = scoped_compose_container("postgres", project)
    mongo_before = scoped_compose_container("mongodb", project)
    network, network_id = scoped_common_network(old, source_before, mongo_before, project)
    old_id, image = old["Id"], old["Image"]
    mongo_mount = next((m for m in mongo_before.get("Mounts", [])
                        if m.get("Destination") == "/data/db"), None)
    if not mongo_mount:
        raise RuntimeError("MongoDB data mount was not observed")
    mount = {"volume_id": mongo_mount.get("Name") or mongo_mount.get("Source"),
             "source": mongo_mount.get("Source"), "destination": mongo_mount.get("Destination")}

    event_window_started = now_ms()
    disconnect = capture(["docker", "network", "disconnect", network, source_before["Id"]])
    if disconnect.returncode and "not connected" not in disconnect.stderr.lower():
        raise RuntimeError("could not deny PostgreSQL network: " + disconnect.stderr.strip())
    network_state = json.loads(run(["docker", "network", "inspect", network]))[0]
    attached_ids = set(network_state.get("Containers", {}))
    if source_before["Id"] in attached_ids:
        raise RuntimeError("PostgreSQL remains attached to the application network")

    run(["docker", "rm", "-f", old_id])
    env = []
    for entry in replacement_environment():
        env.extend(["-e", entry])

    # The monitor starts before the replacement process can make its first socket.
    audit = ContinuousSourceAudit(source_before["Id"])
    new_id = run(["docker", "run", "-d", "--name", "banking-app-replacement",
                  "--label", "com.optivem.cutover.run_id=" + c.run_id,
                  "--network", network, "-p", "8080:8080"] + env + [image])
    audit.set_application(new_id)
    c.container_id = new_id

    deadline = time.monotonic() + 90
    probe_account = accounts.get("preexisting") or next(iter(accounts.values()))
    while time.monotonic() < deadline:
        probe = c.request("GET", "/bank-accounts/" + urllib.parse.quote(probe_account),
                          phase="target_only", route_template=ROUTES["read"][1], account=probe_account)
        if probe["status"] == 200:
            break
        c.traces.pop()
        time.sleep(1)
    else:
        audit._stop.set()
        audit._thread.join(timeout=5)
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

    # Configuration is observed from the running context while the monitor remains active.
    effective = c.internal("GET", "/internal/evidence/configuration")
    inspected = docker_inspect(new_id)
    mongo_after = scoped_compose_container("mongodb", project)
    mongo_mount_after = next((m for m in mongo_after.get("Mounts", [])
                              if m.get("Destination") == "/data/db"), None)
    if not mongo_mount_after:
        raise RuntimeError("MongoDB data mount disappeared")
    mount_after = {"volume_id": mongo_mount_after.get("Name") or mongo_mount_after.get("Source"),
                   "source": mongo_mount_after.get("Source"),
                   "destination": mongo_mount_after.get("Destination")}
    network_after = json.loads(run(["docker", "network", "inspect", network]))[0]
    after_ids = set(network_after.get("Containers", {}))
    if new_id not in after_ids or source_before["Id"] in after_ids:
        raise RuntimeError("scoped network topology changed during audit")

    # Stopping after the final read and topology/configuration observations proves coverage.
    observer, attempts = audit.finish(network_id, effective)
    if not observer["complete"]:
        raise RuntimeError("continuous PostgreSQL isolation audit was incomplete")
    event_window_completed = now_ms()
    destroyed_ms = observed_container_event_ms(
        old_id, "destroy", event_window_started, event_window_completed)
    created_ms = observed_container_event_ms(
        new_id, "create", event_window_started, event_window_completed)
    if not (destroyed_ms <= created_ms and observer["started_ms"] <= created_ms
            and observer["completed_ms"] >= max(
                t["completed_ms"] for t in c.traces if t["trace_id"] in restart_ids)):
        raise RuntimeError("replacement event/audit chronology is incomplete")

    return {
        "old_container_id": old_id, "new_container_id": new_id, "image_digest": image,
        "container_events": [
            {"event": "destroy", "container_id": old_id, "at_ms": destroyed_ms,
             "image_digest": image},
            {"event": "create", "container_id": new_id, "at_ms": created_ms,
             "image_digest": image}],
        "target_mount_before": mount, "target_mount_after": mount_after,
        "postgresql_network_inspect": {
            "network": network, "network_id": network_after["Id"],
            "application_container_id": new_id, "postgresql_container_id": source_before["Id"],
            "attached_container_ids": sorted(after_ids),
            "attached": False, "postgresql_attached": False,
            "source_network_denied": True, "captured_ms": observer["completed_ms"],
            "inspect_command": "docker network inspect " + network},
        "effective_configuration": effective,
        "connection_observer": observer,
        "connection_attempt_log": attempts,
        "restart_trace_ids": restart_ids,
    }

def execute(args):
    workload = json.loads(Path(args.workload).read_text())
    plan = derive_plan(workload)
    run_id = "cutover-%s-%s" % (plan["seed"], now_ms())
    c = Collector(args.base_url, run_id, plan["clock"])
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
    assert_latency_posts_journaled(c.traces, journals, partitions)
    evidence = {"schema_version": 5, "run_id": run_id,
                "migration_intervals": list(unique.values()), "http_trace": c.traces,
                "operation_journal": journals, "mongo_snapshots": snapshots,
                "latency_partitions": partitions, "deployment_evidence": deployment}
    schema_path = ROOT / "tb4_artifacts/candidate_evidence.schema.json"
    schema = json.loads(schema_path.read_text())
    assert_schema_complete(evidence, schema)
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
