#!/usr/bin/env python3
"""Wait for the Compose-v1 cutover stack without depending on Compose-v2 health flags."""
import argparse
import socket
import time
import urllib.error
import urllib.request


def tcp_ready(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def http_ready(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status < 500
    except urllib.error.HTTPError as error:
        return error.code < 500
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    checks = [("postgres", lambda: tcp_ready("127.0.0.1", 5400)),
              ("mongodb", lambda: tcp_ready("127.0.0.1", 27017)),
              ("application", lambda: http_ready(args.base_url + "/internal/cutover/status"))]
    pending = checks
    while pending and time.monotonic() < deadline:
        pending = [(name, check) for name, check in pending if not check()]
        if pending:
            time.sleep(1)
    if pending:
        raise SystemExit("services not ready: " + ", ".join(name for name, _ in pending))
    print("cutover services ready")


if __name__ == "__main__":
    main()
