"""Test ATP TD/MD login directly with the project's native session.

uv run python scripts/test_login.py --timeout 20
Uses temporary flow/storage, sends no orders, and does not start a ZMQ proxy.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from _login_check import (LoginCallbackRegistry, check_session, event_reporter,
                          parser_for, run_cli)


def run_check(args):
    from config import Settings
    from atp_session import AtpSession


    settings = Settings.from_env()
    settings.enable_md = args.md
    settings.initial_symbols = []
    settings.flow_path = Path.cwd() / "flow"

    settings.validate()
    print(f"TD front: {settings.front_td}; MD front: {settings.front_md if args.md else 'disabled'}", flush=True)
    registry = LoginCallbackRegistry()
    session = AtpSession(settings, event_reporter, registry)
    try:
        return check_session(session, args.timeout, args.md, tap=False)
    finally:
        session.close()


def main(argv=None):
    return run_cli(__file__, parser_for("ATP"), run_check, argv)


if __name__ == "__main__":
    raise SystemExit(main())
