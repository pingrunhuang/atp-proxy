"""Shared login diagnostic runner (kept local so each proxy is standalone)."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


def parser_for(name):
    parser = argparse.ArgumentParser(description=f"{name} native TD/MD login diagnostic")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--timeout", type=float, default=20.0, help="Overall login deadline in seconds (plus up to 5s cleanup)")
    parser.add_argument("--md", action=argparse.BooleanOptionalAction, default=True, help="Test MD login as well as TD (default: enabled)")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def event_reporter(topic, event, data):
    # Do not dump account balances, credentials or order/trade payloads.
    if not (topic.startswith("status.") or topic.startswith("errors.")):
        return
    fields = ("status", "reason", "code", "error", "error_id", "error_message", "message")
    details = {key: data[key] for key in fields if key in data}
    print(f"[{event}] {json.dumps(details, ensure_ascii=False, default=str)}", flush=True)


def check_session(session, timeout, md_enabled, *, tap=False):
    """Bound the wait even if native Init/connect blocks; report each leg separately."""
    finished = threading.Event()
    outcome = {}
    started = time.monotonic()

    def connect():
        try:
            outcome["ready"] = session.connect(timeout)
        except Exception as exc:
            outcome["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=connect, name="login-check", daemon=True)
    thread.start()
    previous = {}
    while True:
        states = {
            "TD": (session.td_ready_event if tap else session.td_ready).is_set(),
            "MD": (session.md_ready_event if tap else session.md_ready).is_set(),
        }
        for leg, ready in states.items():
            if leg == "MD" and not md_enabled:
                continue
            if ready and previous.get(leg) != ready:
                print(f"{leg}: READY ({time.monotonic() - started:.3f}s)", flush=True)
        previous = states
        if finished.is_set() or time.monotonic() - started >= timeout:
            break
        finished.wait(min(0.05, max(0, timeout - (time.monotonic() - started))))

    for leg, ready in states.items():
        result = "SKIP (--no-md)" if leg == "MD" and not md_enabled else ("PASS" if ready else "FAIL (not ready)")
        print(f"{leg}: {result}", flush=True)
    if tap:
        print(f"Initial snapshots: {'PASS' if session.initial_sync_event.is_set() else 'FAIL'}", flush=True)
    if "error" in outcome:
        raise outcome["error"]
    success = bool(finished.is_set() and outcome.get("ready") and states["TD"] and (states["MD"] or not md_enabled))
    print(f"Login: {'PASS' if success else 'FAIL / timeout'} ({time.monotonic() - started:.3f}s)", flush=True)
    return 0 if success else 1


class LoginCallbackRegistry:
    """Discard unsolicited order callbacks during login; no database or order submission.

    The native session can receive order/trade notices even when this diagnostic
    submits nothing. Only the callback lookup/update surface is supported.
    """
    def find_by_ctp(self, *args, **kwargs):
        return None

    find_by_atp = find_by_ctp

    def update_ctp(self, **kwargs):
        return None

    update_atp = update_ctp

    def record_trade(self, payload):
        return None


def run_cli(script, parser, worker, argv=None):
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive number")
    args.env_file = args.env_file.expanduser().resolve()
    if args.worker:
        from dotenv import load_dotenv
        from loguru import logger
        load_dotenv(args.env_file, override=False)
        logger.remove()
        logger.add(sys.stderr, level="INFO", backtrace=False, diagnose=False)
        try:
            print(f"Config: {args.env_file} (exists={args.env_file.is_file()}); timeout={args.timeout:g}s", flush=True)
            started = time.monotonic()
            result = worker(args)
            print(f"Result: {'PASS' if result == 0 else 'FAIL'} ({time.monotonic() - started:.3f}s)", flush=True)
            return result
        except Exception as exc:
            print(f"Login: FAIL [{type(exc).__name__}] {exc}", flush=True)
            return 1

    # Isolate SDK crashes, Release() hangs, flow files and SDK-generated logs.
    # The child inherits credentials only via its environment and .env path.
    forwarded = list(sys.argv[1:] if argv is None else argv)
    with tempfile.TemporaryDirectory(prefix="proxy-login-") as directory:
        command = [sys.executable, "-u", str(Path(script).resolve()), *forwarded,
                   "--env-file", str(args.env_file), "--worker"]
        child = subprocess.Popen(command, cwd=directory)
        try:
            code = child.wait(timeout=args.timeout + 5)
            if code < 0:
                print(f"Login: FAIL (native process terminated by signal {-code})", flush=True)
            return 0 if code == 0 else 1
        except subprocess.TimeoutExpired:
            print("Login: FAIL (native startup or cleanup exceeded deadline)", flush=True)
            return 1
        except KeyboardInterrupt:
            return 130
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
