import socket
import sys
from pathlib import Path

import pytest
import zmq


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Settings, normalize_front
from protocol import json_value
from proxy import AtpProxy
from registry import SubscriptionRegistry


class InMemoryOrderRegistry:
    def __init__(self):
        self.orders = {}
        self.trades = []
        self.closed = False

    def create(self, *, client_id, strategy_id, client_order_id, symbol, order_ref, payload):
        key = (client_id, strategy_id, client_order_id)
        if key in self.orders:
            return False
        self.orders[key] = {
            "client_id": client_id,
            "strategy_id": strategy_id,
            "client_order_id": client_order_id,
            "symbol": symbol,
            "order_ref": order_ref,
            "payload": payload,
            "status": "ACCEPTED",
        }
        return True

    def get(self, client_id, strategy_id, client_order_id):
        return self.orders.get((client_id, strategy_id, client_order_id))

    def find_by_atp(self, order_ref="", exchange_id="", order_sys_id=""):
        return next(
            (
                order
                for order in self.orders.values()
                if (order_ref and order.get("order_ref") == order_ref)
                or (
                    exchange_id
                    and order_sys_id
                    and order.get("exchange_id") == exchange_id
                    and order.get("order_sys_id") == order_sys_id
                )
            ),
            None,
        )

    def update_atp(self, *, order_ref, front_id, session_id, exchange_id, order_sys_id, status):
        order = self.find_by_atp(order_ref=order_ref)
        if order:
            order.update(
                front_id=front_id,
                session_id=session_id,
                exchange_id=exchange_id,
                order_sys_id=order_sys_id,
                status=status,
            )

    def list(self, strategy_id=None):
        return [
            order
            for order in self.orders.values()
            if strategy_id is None or order["strategy_id"] == strategy_id
        ]

    def record_trade(self, payload):
        if any(item["event_id"] == payload["event_id"] for item in self.trades):
            return False
        self.trades.append(dict(payload))
        return True

    def list_trades(self, client_id, strategy_id, *, after_id=0, limit=500):
        matching = [
            item
            for item in self.trades
            if item.get("client_id") == client_id
            and item.get("strategy_id") == strategy_id
        ]
        page = matching[after_id : after_id + limit]
        next_after_id = after_id + len(page)
        return {
            "trades": page,
            "next_after_id": next_after_id,
            "has_more": next_after_id < len(matching),
        }


    def latest_trade_cursor(self, client_id, strategy_id):
        return len([
            item
            for item in self.trades
            if item.get("client_id") == client_id
            and item.get("strategy_id") == strategy_id
        ])

    def is_healthy(self):
        return True

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self):
        self.ready = True
        self.subscribed = []
        self.unsubscribed = []
        self.placed = []
        self.cancelled = []
        self.query_calls = []
        self.provider = None
        self.closed = False

    def set_active_symbols_provider(self, provider):
        self.provider = provider

    def connect(self, timeout):
        return True

    def is_ready(self):
        return self.ready

    def subscribe_market_data(self, symbols):
        self.subscribed.append(symbols)

    def unsubscribe_market_data(self, symbols):
        self.unsubscribed.append(symbols)

    def query_account(self, max_age):
        self.query_calls.append(("account", max_age))
        return {"account_id": "test", "balance": 1000}

    def query_positions(self, max_age):
        self.query_calls.append(("positions", max_age))
        return []

    def query_orders(self, max_age):
        self.query_calls.append(("orders", max_age))
        return []

    def place_order(self, request, client_id, strategy_id, client_order_id):
        self.placed.append((request, client_id, strategy_id, client_order_id))
        return {"accepted": True, "client_order_id": client_order_id}

    def cancel_order(self, request, client_id, strategy_id):
        self.cancelled.append((request, client_id, strategy_id))
        return {"accepted": True}

    def close(self):
        self.closed = True


@pytest.fixture
def proxy(tmp_path):
    settings = Settings(
        broker_id="9999",
        user_id="test",
        password="secret",
        front_md="tcp://md",
        front_td="tcp://td",
        database_url="postgresql://unused:unused@localhost/unused",
        snapshot_ttl_seconds=7,
    )
    session = FakeSession()
    instance = AtpProxy(
        settings,
        session=session,
        order_registry=InMemoryOrderRegistry(),
    )
    try:
        yield instance, session
    finally:
        instance.stop()


def test_subscriptions_are_reference_counted_across_strategies(proxy):
    instance, session = proxy
    first = instance.handle_command(
        {"action": "subscribe_market_data", "client_id": "engine", "strategy_id": "a", "symbols": ["au2608"]}
    )
    second = instance.handle_command(
        {"action": "subscribe_market_data", "client_id": "engine", "strategy_id": "b", "symbols": ["au2608"]}
    )
    assert first["data"]["newly_active"] == ["au2608"]
    assert second["data"]["newly_active"] == []
    assert session.subscribed == [["au2608"], []]

    instance.handle_command(
        {"action": "unsubscribe_market_data", "client_id": "engine", "strategy_id": "a", "symbols": ["au2608"]}
    )
    last = instance.handle_command(
        {"action": "unsubscribe_market_data", "client_id": "engine", "strategy_id": "b", "symbols": ["au2608"]}
    )
    assert last["data"]["newly_inactive"] == ["au2608"]
    assert session.unsubscribed == [[], ["au2608"]]


def test_get_trades_is_owner_scoped_and_paginated(proxy):
    instance, _session = proxy
    instance.order_registry.record_trade(
        {
            "event_id": "trade:atp:1",
            "client_id": "engine",
            "strategy_id": "arb-a",
        }
    )
    instance.order_registry.record_trade(
        {
            "event_id": "trade:atp:2",
            "client_id": "engine",
            "strategy_id": "arb-b",
        }
    )

    response = instance.handle_command(
        {
            "action": "get_trades",
            "client_id": "engine",
            "strategy_id": "arb-a",
            "after_id": 0,
            "limit": 10,
        }
    )

    assert response["status"] == "ok"
    assert [item["event_id"] for item in response["data"]["trades"]] == [
        "trade:atp:1"
    ]
    cursor = instance.handle_command(
        {
            "action": "get_trade_cursor",
            "client_id": "engine",
            "strategy_id": "arb-a",
        }
    )
    assert cursor["data"] == {"cursor": 1}


def test_place_order_passes_strategy_identity(proxy):
    instance, session = proxy
    response = instance.handle_command(
        {
            "action": "place_order",
            "request_id": "req-1",
            "client_id": "engine-01",
            "strategy_id": "arb-au",
            "client_order_id": "arb-au-1",
            "symbol": "au2608",
            "exchange": "SHFE",
            "direction": "BUY",
            "offset": "OPEN",
            "price": 780.0,
            "volume": 1,
        }
    )
    assert response["status"] == "ok"
    assert response["request_id"] == "req-1"
    _, client_id, strategy_id, client_order_id = session.placed[0]
    assert (client_id, strategy_id, client_order_id) == ("engine-01", "arb-au", "arb-au-1")


@pytest.mark.parametrize(
    "field,value,message",
    [("volume", 0, "volume must be positive"), ("price", 0, "price must be positive"), ("direction", "HOLD", "HOLD")],
)
def test_place_order_validates_payload(proxy, field, value, message):
    instance, _ = proxy
    request = {
        "action": "place_order",
        "client_id": "engine",
        "strategy_id": "strategy",
        "client_order_id": "strategy-1",
        "symbol": "au2608",
        "direction": "BUY",
        "offset": "OPEN",
        "price": 780,
        "volume": 1,
    }
    request[field] = value
    response = instance.handle_command(request)
    assert response["status"] == "error"
    assert message in response["error"]["message"]


def test_place_order_requires_identity_for_idempotency(proxy):
    instance, _ = proxy
    response = instance.handle_command(
        {
            "action": "place_order",
            "symbol": "au2608",
            "direction": "BUY",
            "offset": "OPEN",
            "price": 780,
            "volume": 1,
        }
    )
    assert response["status"] == "error"
    assert "client_id" in response["error"]["message"]


def test_queries_use_default_cache_ttl_and_force_refresh(proxy):
    instance, session = proxy
    assert instance.handle_command({"action": "get_account"})["status"] == "ok"
    assert instance.handle_command({"action": "get_positions", "force_refresh": True})["status"] == "ok"
    assert session.query_calls == [("account", 7), ("positions", None)]


def test_order_registry_contract_is_idempotent():
    registry = InMemoryOrderRegistry()
    assert registry.create(client_id="c", strategy_id="s", client_order_id="1", symbol="au", order_ref="10", payload="{}")
    assert not registry.create(client_id="c", strategy_id="s", client_order_id="1", symbol="au", order_ref="11", payload="{}")
    registry.update_atp(order_ref="10", front_id=2, session_id=3, exchange_id="SHFE", order_sys_id="sys", status="SUBMITTED")
    assert registry.get("c", "s", "1")["order_sys_id"] == "sys"
    assert registry.find_by_atp(exchange_id="SHFE", order_sys_id="sys")["strategy_id"] == "s"
    registry.close()


def test_protocol_converts_non_finite_atp_prices_to_null():
    assert json_value({"upper_limit": float("inf")}) == {"upper_limit": None}


def test_normalize_front_adds_tcp_scheme():
    assert normalize_front("180.168.146.187:10131") == "tcp://180.168.146.187:10131"
    assert normalize_front("tcp://host:1234") == "tcp://host:1234"


def _free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_zmq_command_round_trip():
    pub_port = _free_port()
    rep_port = _free_port()
    settings = Settings(
        broker_id="9999",
        user_id="test",
        password="secret",
        front_md="tcp://md",
        front_td="tcp://td",
        zmq_bind_host="127.0.0.1",
        zmq_pub_port=pub_port,
        zmq_rep_port=rep_port,
        database_url="postgresql://unused:unused@localhost/unused",
    )
    instance = AtpProxy(
        settings,
        session=FakeSession(),
        order_registry=InMemoryOrderRegistry(),
    )
    context = zmq.Context()
    client = context.socket(zmq.REQ)
    client.setsockopt(zmq.LINGER, 0)
    client.setsockopt(zmq.RCVTIMEO, 2000)
    try:
        instance.start()
        client.connect(f"tcp://127.0.0.1:{rep_port}")
        client.send_json({"action": "ping", "request_id": "round-trip"})
        response = client.recv_json()
        assert response["status"] == "ok"
        assert response["request_id"] == "round-trip"
        assert response["data"]["service"] == "atp-proxy"
        assert response["data"]["md_enabled"] is True
        assert response["data"]["td_enabled"] is True
    finally:
        client.close(0)
        context.term()
        instance.stop()
