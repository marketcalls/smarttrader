import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.broker import order_service
from app.broker.order_service import OrderInputError
from app.models import Base, SymToken


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """Temp symbol master and a fake broker that records calls. No network."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(order_service, "SessionLocal", TestSession)
    with TestSession.begin() as db:
        db.add(SymToken(symbol="SBIN", brsymbol="SBIN-EQ", exchange="NSE", brexchange="NSE", token="3045",
                        tick_size=0.05, instrumenttype="EQ", lotsize=1))
        db.add(SymToken(symbol="CRUDEOIL19OCT26FUT", brsymbol="CRUDEOIL19OCT26", exchange="MCX", brexchange="MCX",
                        token="569900", tick_size=1.0, instrumenttype="FUT", lotsize=100))
    calls: list = []
    book = [
        {"norenordno": "11", "tsym": "SBIN-EQ", "exch": "NSE", "trantype": "B", "qty": "5", "fillshares": "0",
         "prc": "940.00", "prctyp": "LMT", "status": "OPEN", "s_prdt_ali": "MIS"},
        {"norenordno": "12", "tsym": "SBIN-EQ", "exch": "NSE", "trantype": "S", "qty": "5", "prc": "930.00",
         "trgprc": "931.00", "prctyp": "SL-LMT", "status": "TRIGGER_PENDING"},
        {"norenordno": "13", "tsym": "SBIN-EQ", "exch": "NSE", "trantype": "B", "qty": "1", "prc": "950", "prctyp": "LMT",
         "status": "COMPLETE"},
    ]
    positions = [{"tsym": "SBIN-EQ", "exch": "NSE", "prd": "I", "netqty": "-10", "netavgprc": "960.00", "lp": "959.10",
                  "rpnl": "120.00", "urmtom": "9.00", "prcftr": "1.000000"}]

    def fake_post(endpoint, jdata, token):
        calls.append((endpoint, jdata))
        return {"GetQuotes": {"stat": "Ok", "lp": "959.10"}, "OrderBook": book, "PositionBook": positions}.get(
            endpoint, {"stat": "Ok", "norenordno": "99"})

    for name in ("post_authenticated",):
        monkeypatch.setattr(order_service, name, fake_post)
    import app.broker.orders as orders_mod

    monkeypatch.setattr(orders_mod, "post_authenticated", fake_post)
    return calls


def test_market_order_goes_out_as_protected_limit(broker):
    placed = order_service.place("t", "AB1", symbol="sbin", exchange="NSE", side="buy", quantity=2, product="MIS",
                                 price_type="MARKET")
    assert placed.order_id == "99" and placed.sent_price == 963.9
    endpoint, jdata = broker[-1]
    assert endpoint == "PlaceOrder" and jdata["prctyp"] == "LMT" and jdata["prc"] == "963.9" and jdata["qty"] == "2"


def test_validation_messages(broker):
    with pytest.raises(OrderInputError, match="lot size 100"):
        order_service.place("t", "AB1", symbol="CRUDEOIL19OCT26FUT", exchange="MCX", side="BUY", quantity=150,
                            product="NRML", price_type="LIMIT", price=8800)
    with pytest.raises(OrderInputError, match="cannot be placed on NSE_INDEX"):
        order_service.place("t", "AB1", symbol="NIFTY", exchange="NSE_INDEX", side="BUY", quantity=1, product="MIS",
                            price_type="MARKET")
    with pytest.raises(OrderInputError, match="needs a price"):
        order_service.place("t", "AB1", symbol="SBIN", exchange="NSE", side="BUY", quantity=1, product="MIS",
                            price_type="LIMIT")


def test_symbol_state_lists_open_orders_and_position_with_pnl_inputs(broker):
    state = order_service.symbol_state("t", "AB1", "SBIN", "NSE")
    assert [o["order_id"] for o in state["orders"]] == ["11", "12"]
    stop = state["orders"][1]
    assert stop["type"] == "stop_limit" and stop["line_price"] == 931.0
    pos = state["positions"][0]
    assert pos["side"] == "short" and pos["quantity"] == 10 and pos["net_quantity"] == -10
    assert pos["multiplier"] == 1.0 and pos["realized"] == 120.0 and pos["avg_price"] == 960.0


def test_dragging_a_stop_line_moves_trigger_and_keeps_the_gap(broker):
    order_service.modify("t", "AB1", "12", price=935.0)
    endpoint, jdata = broker[-1]
    assert endpoint == "ModifyOrder"
    assert jdata["trgprc"] == "935" and jdata["prc"] == "934" and jdata["prctyp"] == "SL-LMT"


def test_limit_modify_and_completed_order_guard(broker):
    order_service.modify("t", "AB1", "11", price=945.5)
    endpoint, jdata = broker[-1]
    assert endpoint == "ModifyOrder" and jdata["prc"] == "945.5" and "trgprc" not in jdata and jdata["qty"] == "5"
    with pytest.raises(OrderInputError, match="complete"):
        order_service.modify("t", "AB1", "13", price=951)
