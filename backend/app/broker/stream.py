"""Live ticks from the TradeSmart (Noren) WebSocket, relayed to one browser connection.

The browser never sees the access token: the backend opens the broker socket,
sends the connect request (t=a) with the stored token, subscribes touchline
(t=t) for the instrument the browser asks for, keeps the socket alive with a
heartbeat (t=h) every 30 seconds and forwards only price and volume.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import websockets

from app.config import STREAM_URL, USER_AGENT

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30
CONNECT_TIMEOUT = 15


class BrowserChannel(Protocol):
    async def send_json(self, data: Any) -> None: ...

    async def receive_json(self) -> Any: ...


class StreamError(Exception):
    """The broker refused the stream (bad session) or the socket failed."""


@dataclass
class Instrument:
    symbol: str
    exchange: str
    key: str  # "EXCH|token" as the broker expects it


@dataclass
class TickState:
    """Touchline feeds after the first one carry only changed fields."""

    ltp: float | None = None
    volume: float | None = None


def _num(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def merge_tick(state: TickState, message: dict[str, Any], now: float | None = None) -> dict[str, Any] | None:
    """Fold one touchline message into the running state; return the tick to forward, if any."""
    ltp = _num(message.get("lp"))
    volume = _num(message.get("v"))
    if ltp is not None and ltp > 0:
        state.ltp = ltp
    if volume is not None and volume >= 0:
        state.volume = volume
    if state.ltp is None:
        return None
    # "ft" is the last trade time and goes stale between trades, so ticks carry the server clock.
    return {"ltp": state.ltp, "volume": state.volume, "time": int(now if now is not None else time.time())}


async def relay_ticks(
    browser: BrowserChannel,
    *,
    user_id: str,
    access_token: str,
    resolve,
) -> None:
    """Run until the browser or the broker goes away.

    `resolve(symbol, exchange)` returns an Instrument or None (unknown symbol).
    """
    try:
        broker = await websockets.connect(
            STREAM_URL,
            user_agent_header=USER_AGENT,
            open_timeout=CONNECT_TIMEOUT,
            ping_interval=None,
        )
    except (OSError, websockets.WebSocketException, TimeoutError) as exc:
        raise StreamError(f"Could not reach the TradeSmart stream ({exc.__class__.__name__})") from exc

    async with broker:
        await broker.send(
            json.dumps({"t": "a", "uid": user_id, "actid": user_id, "source": "API", "accesstoken": access_token})
        )
        try:
            ack = json.loads(await asyncio.wait_for(broker.recv(), CONNECT_TIMEOUT))
        except (TimeoutError, ValueError, websockets.WebSocketException) as exc:
            raise StreamError("The TradeSmart stream did not acknowledge the connection") from exc
        # The docs say "Ok"; the live service answers "OK".
        if not isinstance(ack, dict) or ack.get("t") != "ak" or str(ack.get("s", "")).lower() != "ok":
            raise StreamError("TradeSmart refused the stream. Your session may have expired; log in again.")
        await browser.send_json({"type": "status", "state": "connected"})

        current: Instrument | None = None
        state = TickState()

        async def from_browser() -> None:
            nonlocal current, state
            while True:
                message = await browser.receive_json()
                if not isinstance(message, dict) or message.get("action") != "subscribe":
                    continue
                symbol = str(message.get("symbol", "")).strip().upper()
                exchange = str(message.get("exchange", "")).strip().upper()
                instrument = await resolve(symbol, exchange)
                if instrument is None:
                    await browser.send_json({"type": "error", "message": f"{symbol} on {exchange} is not in the master contract"})
                    continue
                if current and current.key != instrument.key:
                    await broker.send(json.dumps({"t": "u", "k": current.key}))
                current, state = instrument, TickState()
                await broker.send(json.dumps({"t": "t", "k": instrument.key}))
                await browser.send_json({"type": "subscribed", "symbol": symbol, "exchange": exchange})

        async def from_broker() -> None:
            async for raw in broker:
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(message, dict) or message.get("t") not in ("tk", "tf") or current is None:
                    continue
                if f"{message.get('e')}|{message.get('tk')}" != current.key:
                    continue
                tick = merge_tick(state, message)
                if tick:
                    await browser.send_json({"type": "tick", "symbol": current.symbol, "exchange": current.exchange, **tick})
            raise StreamError("The TradeSmart stream closed")

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                await broker.send(json.dumps({"t": "h"}))

        tasks = [asyncio.create_task(c()) for c in (from_browser, from_broker, heartbeat)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
