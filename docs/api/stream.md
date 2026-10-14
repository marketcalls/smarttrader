# Live ticks WebSocket

Source: `backend/app/routers/stream.py`, `backend/app/broker/stream.py`.
Tests: `backend/tests/test_stream.py`.

Back to the [API index](README.md).

---

## WS /api/ws/ticks

Relays live last-traded price and day volume for one instrument at a time from the TradeSmart (Noren)
WebSocket to the browser. The backend holds the broker socket, so the access token never reaches the
browser. Each browser connection gets its own broker connection.

- URL: `ws://127.0.0.1:8000/api/ws/ticks` (or through the Vite proxy on port 5173)
- Auth required: yes, the `st_session` cookie on the handshake
- Origin check: required (see below)
- All messages are JSON text frames.

### Handshake and close codes

1. Origin check, before accepting. The `Origin` header must be present and either be one of
   `http://127.0.0.1:5173`, `http://localhost:5173`, or have the same `host:port` as the request's
   `Host` header (for example `http://127.0.0.1:8000` when the UI is served by the backend).
   Otherwise the server closes before accepting with code `4403` and reason `Origin not allowed`. A
   browser sees this as a failed handshake (HTTP 403).
2. The server looks up the session and decrypts its token in a thread pool, then accepts the socket.
3. Without an active session, user id or decryptable token, the server
   closes with code `4401` and reason `Not authenticated`. Accepting first means the browser receives
   4401 and can stop reconnecting.
4. The backend connects to the broker and authenticates. On success it sends a `status` /
   `connected` message.

| Close code | Reason | Meaning |
| --- | --- | --- |
| 4403 | `Origin not allowed` | Missing or foreign `Origin` (sent before accept) |
| 4401 | `Not authenticated` | No active session |
| 1011 | none | Broker refused the stream, the broker socket failed or closed. A `status` / `error` message precedes it when possible |
| 1000 / 1001 | | Normal close by the browser |

### Client to server messages

Subscribe (replaces any previous subscription on this connection):

```json
{"action": "subscribe", "symbol": "SBIN", "exchange": "NSE"}
```

| Field | Type | Notes |
| --- | --- | --- |
| `action` | string | Must be `subscribe`. Any other message, or a non-object, is ignored. |
| `symbol` | string | Trimmed and upper-cased, looked up in `symtoken` |
| `exchange` | string | Trimmed and upper-cased. Index exchanges are allowed and are streamed from NSE or BSE. |

There is no unsubscribe action; subscribe to another instrument or close the socket.

### Server to client messages

Connected (once, after the broker acknowledged the login):

```json
{"type": "status", "state": "connected"}
```

Subscribed (after each successful subscribe; echoes the normalized request values):

```json
{"type": "subscribed", "symbol": "SBIN", "exchange": "NSE"}
```

Unknown instrument (the connection stays open):

```json
{"type": "error", "message": "SBINX on NSE is not in the master contract"}
```

An unknown instrument leaves the existing subscription active. Subscribing to the current instrument
again sends another broker subscribe and resets cached LTP/volume; no unsubscribe is sent for the
same broker key. The next tick waits for a new positive LTP.

Tick:

```json
{"type": "tick", "symbol": "SBIN", "exchange": "NSE", "ltp": 952.45, "volume": 8123456.0, "time": 1760415123}
```

| Field | Type | Notes |
| --- | --- | --- |
| `symbol`, `exchange` | string | From the `symtoken` row of the current subscription |
| `ltp` | number | Last traded price. Updates only when the broker sends `lp > 0`, otherwise the previous value is repeated |
| `volume` | number or null | Cumulative day volume (`v`). Null until the broker has sent one |
| `time` | integer | Server clock in epoch seconds at the moment of relay (not the broker's last trade time) |

No tick is sent until an LTP is known. Ticks are only sent for the current subscription; feeds for
other tokens are dropped.

Stream failure (followed by close 1011):

```json
{"type": "status", "state": "error", "message": "TradeSmart refused the stream. Your session may have expired; log in again."}
```

Possible `message` values:

- `Could not reach the TradeSmart stream (<ExceptionName>)`
- `The TradeSmart stream did not acknowledge the connection`
- `TradeSmart refused the stream. Your session may have expired; log in again.`
- `The TradeSmart stream closed`

### Example session

```
client -> (handshake with Cookie: st_session=..., Origin: http://127.0.0.1:5173)
server <- {"type": "status", "state": "connected"}
client -> {"action": "subscribe", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX"}
server <- {"type": "subscribed", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX"}
server <- {"type": "tick", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX", "ltp": 8801.0, "volume": 21951.0, "time": 1760415123}
server <- {"type": "tick", "symbol": "CRUDEOIL19OCT26FUT", "exchange": "MCX", "ltp": 8802.0, "volume": 21951.0, "time": 1760415124}
```

### Side effects

- Opens one outbound WebSocket to `wss://v2api.tradesmartonline.in/NorenWSAPI/` per browser
  connection, sends the connect request (`t=a`), touchline subscribe (`t=t`), unsubscribe (`t=u`) when
  switching instruments, and a heartbeat (`t=h`) every 30 seconds (see
  [broker-integration.md](broker-integration.md#websocket-norenwsapi)).
- Reads `auth_session` and `symtoken`. No database writes. The session is not deactivated when the
  broker refuses the stream.
- Credentials are checked only on connection. Logout or a new broker login does not revalidate or
  close an already connected socket.

### Notes and possible issues

- A text frame from the browser that is not valid JSON makes `receive_json` raise a decode error that
  the router does not catch, so the connection ends with an unhandled server error instead of the
  message being ignored.
- Ticks for a feed are matched on `<e>|<tk>` from the broker message. Index subscriptions use the
  parent exchange (`NSE|<token>`), and the `e` field in the broker feed must match that.
