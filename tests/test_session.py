import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import atp_session
from atp_session import AtpSession, AtpTraderSpi
from config import Settings


class MemoryRegistry:
    def __init__(self):
        self.trades = []

    def find_by_atp(self, *_args, **_kwargs):
        return None

    def update_atp(self, **_kwargs):
        return None

    def record_trade(self, payload):
        self.trades.append(dict(payload))
        return True


def settings(tmp_path):
    return Settings(
        broker_id="broker",
        user_id="user",
        password="password",
        front_md="tcp://md:1",
        front_td="tcp://td:2",
        flow_path=tmp_path / "flow",
        database_url="postgresql://unused:unused@localhost/unused",
    )


class FakeApi:
    def __init__(self):
        self.spi = None
        self.front = None
        self.private_topic = None
        self.public_topic = None

    def RegisterSpi(self, spi):
        self.spi = spi

    def RegisterFront(self, front):
        self.front = front

    def SubscribePrivateTopic(self, mode):
        self.private_topic = mode

    def SubscribePublicTopic(self, mode):
        self.public_topic = mode

    def Init(self):
        return None


def test_v6315_factories_receive_only_flow_path(monkeypatch, tmp_path):
    calls = {}
    md_instance = FakeApi()
    td_instance = FakeApi()

    class MdFactory:
        @staticmethod
        def CreateFtdcMdApi(*args):
            calls["md"] = args
            return md_instance

    class TdFactory:
        @staticmethod
        def CreateFtdcTraderApi(*args):
            calls["td"] = args
            return td_instance

    monkeypatch.setattr(atp_session, "ATP_AVAILABLE", True)
    monkeypatch.setattr(atp_session.mdapi, "CThostFtdcMdApi", MdFactory, raising=False)
    monkeypatch.setattr(atp_session.tdapi, "CThostFtdcTraderApi", TdFactory, raising=False)
    monkeypatch.setattr(atp_session.tdapi, "THOST_TERT_RESTART", 0, raising=False)

    session = AtpSession(settings(tmp_path), lambda *_args: None, MemoryRegistry())
    session.md_ready.set()
    session.td_ready.set()

    assert session.connect(0.01)
    assert len(calls["md"]) == 1
    assert len(calls["td"]) == 1
    assert calls["md"][0].endswith("/md/")
    assert calls["td"][0].endswith("/td/")
    assert md_instance.front == "tcp://md:1"
    assert td_instance.front == "tcp://td:2"


def test_trader_callback_runs_login_settlement_sequence(monkeypatch, tmp_path):
    class Field:
        pass

    for field_name in (
        "CThostFtdcReqUserLoginField",
        "CThostFtdcSettlementInfoConfirmField",
    ):
        monkeypatch.setattr(atp_session.tdapi, field_name, Field, raising=False)

    calls = []

    class FakeTraderApi:
        def ReqUserLogin(self, request, request_id):
            calls.append(("login", request, request_id))
            return 0

        def ReqSettlementInfoConfirm(self, request, request_id):
            calls.append(("settlement", request, request_id))
            return 0

    session = AtpSession(settings(tmp_path), lambda *_args: None, MemoryRegistry())
    session.td_api = FakeTraderApi()
    spi = AtpTraderSpi(session)

    spi.OnFrontConnected()
    spi.OnRspUserLogin(
        SimpleNamespace(FrontID=7, SessionID=8, MaxOrderRef="9"),
        None,
        1,
        True,
    )
    spi.OnRspSettlementInfoConfirm(None, None, 2, True)

    assert [name for name, *_rest in calls] == [
        "login",
        "settlement",
    ]
    assert calls[0][1].BrokerID == "broker"
    assert calls[0][1].UserID == "user"
    assert calls[0][1].Password == "password"
    assert calls[1][1].InvestorID == "user"
    assert session.td_ready.is_set()


def test_trade_payload_has_stable_native_fill_identity(tmp_path):
    class OwnedRegistry(MemoryRegistry):
        def find_by_atp(self, *_args, **_kwargs):
            return {
                "client_id": "engine-01",
                "strategy_id": "arb-atp",
                "client_order_id": "arb-atp-1",
            }

    session = AtpSession(
        settings(tmp_path),
        lambda *_args: None,
        OwnedRegistry(),
    )
    fill = SimpleNamespace(
        OrderRef="7",
        ExchangeID="LME",
        OrderSysID="ORDER-9",
        InstrumentID="NI3M-LME",
        TradeID="TRADE-1",
        Direction="0",
        OffsetFlag="0",
        Price=15123.5,
        Volume=2,
        TradeTime="10:31:05",
        TradingDay="20260814",
    )

    first = session.trade_payload(fill)
    repeated = session.trade_payload(fill)
    different = session.trade_payload(
        SimpleNamespace(**{**vars(fill), "TradeID": "TRADE-2"})
    )

    assert first["event_id"] == repeated["event_id"]
    assert first["event_id"].startswith("trade:atp:")
    assert first["event_id"] != different["event_id"]
    assert first["gateway_name"] == "ATP"
    assert first["account_id"] == "user"
    assert first["trading_day"] == "20260814"
    assert first["exchange"] == "LME"
    assert first["trade_id"] == "TRADE-1"
    assert first["client_id"] == "engine-01"
    assert first["strategy_id"] == "arb-atp"
    assert first["client_order_id"] == "arb-atp-1"


def test_trade_is_persisted_before_publish(tmp_path):
    registry = MemoryRegistry()
    published = []
    session = AtpSession(
        settings(tmp_path),
        lambda topic, event, data: published.append((topic, event, data)),
        registry,
    )
    payload = {
        "event_id": "trade:atp:persisted-1",
        "client_id": "engine",
        "strategy_id": "arb-atp",
    }

    session.publish_trade(payload)

    assert registry.trades == [payload]
    assert published[0] == (
        "trades.user",
        "trade",
        {**payload, "trade_cursor": 1},
    )
