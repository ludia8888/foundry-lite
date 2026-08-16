"""Bounded readiness wait used by bootstrap and migration Jobs."""

from __future__ import annotations

import argparse
import json
import socket
import time


def wait_for_tcp(host: str, port: int, *, timeout_seconds: float) -> bool:
    if not host or len(host) > 253 or port < 1 or port > 65535:
        raise ValueError("tcp_target_invalid")
    if timeout_seconds <= 0 or timeout_seconds > 600:
        raise ValueError("tcp_timeout_invalid")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=min(2.0, timeout_seconds)):
                return True
        except OSError:
            time.sleep(1.0)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait for a TCP endpoint without sending application data.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        is_ready = wait_for_tcp(args.host, args.port, timeout_seconds=args.timeout_seconds)
    except ValueError:
        print(json.dumps({"status": "failed", "reason": "invalid_tcp_wait_configuration"}))
        return 2
    print(json.dumps({"status": "ready" if is_ready else "timeout", "host": args.host, "port": args.port}))
    return 0 if is_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
