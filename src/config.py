from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y"})


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in TRUE_VALUES


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _symbols_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def normalize_front(value: str) -> str:
    value = value.strip()
    if value and "://" not in value:
        return f"tcp://{value}"
    return value


@dataclass(slots=True)
class Settings:
    broker_id: str
    user_id: str
    password: str
    front_md: str
    front_td: str
    enable_md: bool = True
    initial_symbols: list[str] = field(default_factory=list)
    zmq_bind_host: str = "0.0.0.0"
    zmq_pub_port: int = 5585
    zmq_rep_port: int = 5586
    query_min_interval_seconds: float = 1.0
    query_timeout_seconds: float = 10.0
    snapshot_ttl_seconds: float = 5.0
    flow_path: Path = Path("flow")
    database_url: str = "postgresql://atp_proxy:atp_proxy@127.0.0.1:5432/atp_proxy"
    database_pool_min_size: int = 1
    database_pool_max_size: int = 5
    database_connect_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            broker_id=os.getenv("ATP_BROKER_ID", ""),
            user_id=os.getenv("ATP_USER_ID", ""),
            password=os.getenv("ATP_PASSWORD", ""),
            front_md=normalize_front(os.getenv("ATP_FRONT_MD", "")),
            front_td=normalize_front(os.getenv("ATP_FRONT_TD", "")),
            enable_md=_bool_env("ATP_ENABLE_MD", True),
            initial_symbols=_symbols_env("ATP_SYMBOLS"),
            zmq_bind_host=os.getenv("ZMQ_BIND_HOST", "0.0.0.0"),
            zmq_pub_port=_int_env("ZMQ_PUB_PORT", 5585),
            zmq_rep_port=_int_env("ZMQ_REP_PORT", 5586),
            query_min_interval_seconds=_float_env("ATP_QUERY_MIN_INTERVAL_SECONDS", 1.0),
            query_timeout_seconds=_float_env("ATP_QUERY_TIMEOUT_SECONDS", 10.0),
            snapshot_ttl_seconds=_float_env("ATP_SNAPSHOT_TTL_SECONDS", 5.0),
            flow_path=Path(os.getenv("ATP_FLOW_PATH", "flow")),
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://atp_proxy:atp_proxy@127.0.0.1:5432/atp_proxy",
            ),
            database_pool_min_size=_int_env("DATABASE_POOL_MIN_SIZE", 1),
            database_pool_max_size=_int_env("DATABASE_POOL_MAX_SIZE", 5),
            database_connect_timeout_seconds=_float_env(
                "DATABASE_CONNECT_TIMEOUT_SECONDS", 10.0
            ),
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in {
                "ATP_BROKER_ID": self.broker_id,
                "ATP_USER_ID": self.user_id,
                "ATP_PASSWORD": self.password,
                "ATP_FRONT_TD": self.front_td,
                "DATABASE_URL": self.database_url,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        if not self.enable_md:
            if self.initial_symbols:
                raise ValueError("ATP_SYMBOLS must be empty when ATP_ENABLE_MD=false")
        elif not self.front_md:
            raise ValueError("Missing required configuration: ATP_FRONT_MD")
        if not self.database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
        if self.database_pool_min_size < 1:
            raise ValueError("DATABASE_POOL_MIN_SIZE must be at least 1")
        if self.database_pool_max_size < self.database_pool_min_size:
            raise ValueError("DATABASE_POOL_MAX_SIZE must be >= DATABASE_POOL_MIN_SIZE")
        if not 1 <= self.zmq_pub_port <= 65535 or not 1 <= self.zmq_rep_port <= 65535:
            raise ValueError("ZeroMQ ports must be between 1 and 65535")
        if self.zmq_pub_port == self.zmq_rep_port:
            raise ValueError("ZMQ_PUB_PORT and ZMQ_REP_PORT must be different")
        if self.query_min_interval_seconds < 0 or self.query_timeout_seconds <= 0:
            raise ValueError("ATP query intervals must be non-negative and timeout must be positive")
