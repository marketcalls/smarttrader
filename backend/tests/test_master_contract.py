import io

from app.broker.master_contract import (
    format_derivative_symbol,
    format_expiry,
    handle_strike,
    parse_bfo,
    parse_bse,
    parse_cds,
    parse_mcx,
    parse_nfo,
    parse_nse,
)


def lines(text: str) -> io.StringIO:
    return io.StringIO(text.lstrip("\n"), newline="")


def by_token(rows):
    return {r["token"]: r for r in rows}


NSE_SAMPLE = """
Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,
NSE,26000,1,Nifty 50,NIFTY INDEX,INDEX,0,
NSE,26009,1,Nifty Bank,NIFTY BANK,INDEX,0,
NSE,26017,1,INDIAVIX,INDIA VIX,INDEX,0,
NSE,26037,1,Nifty Fin Services,FINNIFTY,INDEX,0,
NSE,2885,1,RELIANCE,RELIANCE-EQ,EQ,0.1,
NSE,16669,1,BAJAJ-AUTO,BAJAJ-AUTO-EQ,EQ,1,
NSE,2031,1,M&M,M&M-EQ,EQ,0.1,
NSE,9999,1,SOMEBE,SOMEBE-BE,BE,0.01,
NSE,7777,1,GSEC,GS2030-GS,GS,0.01,
"""


def test_parse_nse():
    rows = by_token(parse_nse(lines(NSE_SAMPLE)))
    assert len(rows) == 9

    rel = rows["2885"]
    assert rel == {
        "symbol": "RELIANCE", "brsymbol": "RELIANCE-EQ", "name": "RELIANCE",
        "exchange": "NSE", "brexchange": "NSE", "token": "2885", "expiry": "",
        "strike": -1, "lotsize": 1, "instrumenttype": "EQ", "tick_size": 0.1,
    }
    assert rows["16669"]["symbol"] == "BAJAJ-AUTO"
    assert rows["2031"]["symbol"] == "M&M"
    assert rows["9999"]["symbol"] == "SOMEBE"
    assert rows["9999"]["instrumenttype"] == "EQ"
    assert rows["7777"]["instrumenttype"] == "GS"

    assert rows["26000"]["symbol"] == "NIFTY"
    assert rows["26009"]["symbol"] == "BANKNIFTY"
    assert rows["26017"]["symbol"] == "INDIAVIX"
    assert rows["26037"]["symbol"] == "FINNIFTY"
    for token in ("26000", "26009", "26017", "26037"):
        assert rows[token]["exchange"] == "NSE_INDEX"
        assert rows[token]["brexchange"] == "NSE_INDEX"
        assert rows[token]["instrumenttype"] == "INDEX"
    assert rows["26000"]["brsymbol"] == "NIFTY INDEX"


def test_parse_bse_adds_indices():
    sample = """
Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,
BSE,500325,1,RELIANCE,RELIANCE,A,0.05,
BSE,978290,1,RRARPL30,RRARPL30,F,0.01,
"""
    rows = parse_bse(lines(sample))
    eq = by_token([r for r in rows if r["exchange"] == "BSE"])
    assert eq["500325"]["symbol"] == "RELIANCE"
    assert eq["500325"]["instrumenttype"] == "EQ"
    assert eq["500325"]["tick_size"] == 0.05
    assert eq["978290"]["instrumenttype"] == "EQ"
    idx = {r["symbol"]: r for r in rows if r["exchange"] == "BSE_INDEX"}
    assert set(idx) == {"SENSEX", "BANKEX"}
    assert idx["SENSEX"]["token"] == "1"
    assert idx["BANKEX"]["token"] == "12"
    assert idx["SENSEX"]["instrumenttype"] == "INDEX"


def test_parse_nfo():
    sample = """
Exchange,Token,LotSize,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,
NFO,61471,65,NIFTY,NIFTY23NOV26F,23-NOV-2026,FUTIDX,XX,-0.01,0.1,
NFO,40095,65,NIFTY,NIFTY09NOV26C26000,09-NOV-2026,OPTIDX,CE,26000,0.05,
NFO,151104,900,ZYDUSLIFE,ZYDUSLIFE29DEC26P1640,29-DEC-2026,OPTSTK,PE,1640,0.05,
NFO,151200,900,ABC,ABC29DEC26P12.5,29-DEC-2026,OPTSTK,PE,12.5,0.05,
"""
    rows = by_token(parse_nfo(lines(sample)))
    fut = rows["61471"]
    assert fut["symbol"] == "NIFTY23NOV26FUT"
    assert fut["instrumenttype"] == "FUT"
    assert fut["expiry"] == "23-NOV-26"
    assert fut["lotsize"] == 65
    assert fut["exchange"] == fut["brexchange"] == "NFO"

    ce = rows["40095"]
    assert ce["symbol"] == "NIFTY09NOV2626000CE"
    assert ce["brsymbol"] == "NIFTY09NOV26C26000"
    assert ce["strike"] == 26000 and isinstance(ce["strike"], int)
    assert ce["instrumenttype"] == "CE"
    assert ce["tick_size"] == 0.05

    assert rows["151104"]["symbol"] == "ZYDUSLIFE29DEC261640PE"
    assert rows["151200"]["symbol"] == "ABC29DEC2612.5PE"


def test_parse_bfo_strike_column_and_trailing_comma():
    sample = """
Exchange,Token,LotSize,Symbol,TradingSymbol,Expiry,Instrument,OptionType,Strike,TickSize,
BFO,1134977,2425,ZOMAOPT,ETERNAL26OCT375PE,29-OCT-2026,OPTSTK,PE,375,0.05,
BFO,863548,2425,ZOMAFUT,ETERNAL26OCTFUT,29-OCT-2026,FUTSTK,XX,0,0.05,
BFO,900001,20,SENSEXOPT,SENSEX26OCT82000CE,29-OCT-2026,OPTIDX,CE,82000,0.05,
BFO,900002,20,SENSEXOPT,SENSEX26OCT82000.5CE,29-OCT-2026,OPTIDX,CE,82000.5,0.05,
"""
    rows = by_token(parse_bfo(lines(sample)))
    pe = rows["1134977"]
    assert pe["name"] == "ETERNAL"
    assert pe["symbol"] == "ETERNAL29OCT26375PE"
    assert pe["strike"] == 375
    assert pe["instrumenttype"] == "PE"
    assert pe["expiry"] == "29-OCT-26"

    fut = rows["863548"]
    assert fut["symbol"] == "ETERNAL29OCT26FUT"
    assert fut["instrumenttype"] == "FUT"

    assert rows["900001"]["symbol"] == "SENSEX29OCT2682000CE"
    assert rows["900002"]["symbol"] == "SENSEX29OCT2682000.5CE"
    assert all(r["exchange"] == "BFO" for r in rows.values())


def test_parse_cds_filters_small_tokens_and_maps_options():
    sample = """
Exchange,Token,LotSize,Precision,Multiplier,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,
CDS,1,1,4,1000,USDINR,USDINR,,INDEX,XX,,0.0025
CDS,13298,1,4,1000,USDINR,USDINR13NOV26F,13-NOV-2026,FUTCUR,XX,-1e-07,0.0025
CDS,17274,1,4,1000,USDJPY,USDJPY29DEC26P141,29-DEC-2026,OPTCUR,PE,141,0.01
CDS,17280,1,4,1000,USDINR,USDINR29DEC26C82.5,29-DEC-2026,OPTCUR,CE,82.5,0.0025
"""
    rows = by_token(parse_cds(lines(sample)))
    assert "1" not in rows
    assert rows["13298"]["symbol"] == "USDINR13NOV26FUT"
    assert rows["13298"]["instrumenttype"] == "FUT"
    assert rows["17274"]["symbol"] == "USDJPY29DEC26141PE"
    assert rows["17274"]["instrumenttype"] == "PE"
    assert rows["17280"]["symbol"] == "USDINR29DEC2682.5CE"
    assert rows["17280"]["tick_size"] == 0.0025


def test_parse_mcx():
    sample = """
Exchange,Token,LotSize,GNGD,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,PriceUnits,TradeUnits,DeliveryUnits,
MCX,569900,100,1,CRUDEOIL,CRUDEOIL19OCT26,19-OCT-2026,FUTCOM,XX,0,1,BBL,BBL,BBL,
MCX,586425,10,1,CRUDEOILM,CRUDEOILM16DEC26P5300,16-DEC-2026,OPTFUT,PE,5300,0.05,BBL,BBL,BBL,
MCX,600001,1,1,MCXBULLDEX,MCXBULLDEX24OCT26,24-OCT-2026,FUTIDX,XX,0,1,,,,
"""
    rows = by_token(parse_mcx(lines(sample)))
    crude = rows["569900"]
    assert crude["symbol"] == "CRUDEOIL19OCT26FUT"
    assert crude["instrumenttype"] == "FUT"
    assert crude["lotsize"] == 100
    assert crude["exchange"] == "MCX"
    assert rows["586425"]["symbol"] == "CRUDEOILM16DEC265300PE"
    assert rows["586425"]["instrumenttype"] == "PE"
    assert rows["600001"]["instrumenttype"] == "FUT"


def test_helpers():
    assert format_expiry("29-DEC-2026") == "29-DEC-26"
    assert format_expiry("29-Dec-2026") == "29-DEC-26"
    assert format_expiry("") is None
    assert format_expiry("2026-12-29") is None
    assert handle_strike("26000") == 26000
    assert handle_strike("26000.0") == 26000
    assert handle_strike("82.5") == 82.5
    assert handle_strike("") == -1
    assert handle_strike("abc") == -1
    assert format_derivative_symbol("NIFTY", "29-DEC-26", 26000, "CE") == "NIFTY29DEC2626000CE"
    assert format_derivative_symbol("NIFTY", "29-DEC-26", -0.01, "FUT") == "NIFTY29DEC26FUT"
    assert format_derivative_symbol("X", None, 10.0, "PE") == "X10PE"


def test_blank_rows_are_skipped():
    sample = """
Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,
NSE,,1,BAD,,EQ,0.01,

NSE,2885,1,RELIANCE,RELIANCE-EQ,EQ,0.1,
"""
    rows = parse_nse(lines(sample))
    assert [r["token"] for r in rows] == ["2885"]
