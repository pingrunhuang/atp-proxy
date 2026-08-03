from __future__ import annotations

import argparse
import signal
import threading

from dotenv import load_dotenv
from loguru import logger

from config import Settings
from logger import configure_logger
from proxy import AtpProxy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ATP ZeroMQ proxy")
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    configure_logger()
    args = parse_args()
    settings = Settings.from_env()
    settings.validate()
    proxy = AtpProxy(settings)
    shutdown = threading.Event()

    def request_shutdown(signum: int, _frame: object) -> None:
        logger.info("Received signal {}; shutting down", signum)
        shutdown.set()

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    try:
        proxy.start()
        if not proxy.connect(args.connect_timeout):
            logger.error("ATP login did not complete before timeout")
            return 1
        logger.info("ATP proxy is ready")
        shutdown.wait()
        return 0
    except Exception:
        logger.exception("ATP proxy terminated unexpectedly")
        return 1
    finally:
        proxy.stop()


if __name__ == "__main__":
    raise SystemExit(main())

