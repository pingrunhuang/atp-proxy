import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Settings, normalize_front


def valid_settings(**overrides):
    values = {
        "broker_id": "broker",
        "user_id": "user",
        "password": "password",
        "front_md": "tcp://md:1",
        "front_td": "tcp://td:2",
        "database_url": "postgresql://user:pass@db/name",
    }
    values.update(overrides)
    return Settings(**values)


def test_normalize_front_adds_tcp_scheme():
    assert normalize_front("md.example:1234") == "tcp://md.example:1234"
    assert normalize_front("tcp://md.example:1234") == "tcp://md.example:1234"


def test_from_env_uses_atp_names_and_distinct_ports(monkeypatch):
    monkeypatch.setenv("ATP_BROKER_ID", "b")
    monkeypatch.setenv("ATP_USER_ID", "u")
    monkeypatch.setenv("ATP_PASSWORD", "p")
    monkeypatch.setenv("ATP_FRONT_MD", "md:1")
    monkeypatch.setenv("ATP_FRONT_TD", "td:2")
    settings = Settings.from_env()
    assert settings.front_md == "tcp://md:1"
    assert settings.front_td == "tcp://td:2"
    assert (settings.zmq_pub_port, settings.zmq_rep_port) == (5585, 5586)


def test_validate_rejects_missing_credentials():
    with pytest.raises(ValueError, match="ATP_PASSWORD"):
        valid_settings(password="").validate()


def test_validate_rejects_same_ports():
    with pytest.raises(ValueError, match="must be different"):
        valid_settings(zmq_pub_port=5585, zmq_rep_port=5585).validate()
