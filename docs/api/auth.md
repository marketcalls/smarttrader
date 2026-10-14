# Auth, config and app routes

Source: `backend/app/routers/auth.py`, `backend/app/main.py`, `backend/app/deps.py`.
Tests: `backend/tests/test_callback.py`, `backend/tests/test_checksum.py`.

Back to the [API index](README.md).

---

## GET /api/health

Liveness check.

- Auth required: no
- Request: none

Response `200`:

```json
{"status": "ok"}
```

Errors: none.
Side effects: none.

---

## GET /api/config

Returns the saved TradeSmart app configuration so the login page can pre-fill its fields. The API
secret itself is never returned, only whether one is stored.

- Auth required: no
- Request: none

Response `200` when a config row exists:

```json
{
  "api_key": "<API_KEY>",
  "redirect_url": "http://127.0.0.1:8000/tradesmart/callback",
  "has_secret": true
}
```

Response `200` before anything was saved:

```json
{"api_key": null, "redirect_url": null, "has_secret": false}
```

| Field | Type | Notes |
| --- | --- | --- |
| `api_key` | string or null | TradeSmart API key (client id) as saved |
| `redirect_url` | string or null | Redirect URL as saved |
| `has_secret` | boolean | true when an encrypted API secret is stored |

Errors: none.
Side effects: none (reads `broker_config` row id 1).

---

## POST /api/auth/login

Saves the broker credentials and returns the TradeSmart OAuth authorize URL. The frontend then sets
`window.location.href` to that URL.

- Auth required: no

Request body (`LoginRequest`):

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `api_key` | string | yes | Trimmed. Must not be empty. |
| `api_secret` | string or null | no (default null) | Trimmed. Empty or null keeps the already stored secret. Required when no secret is stored yet. |
| `redirect_url` | string | yes | Trimmed. Must be an `http` or `https` URL with a host. Must match the URL registered with TradeSmart. |

Example request:

```json
{
  "api_key": "<API_KEY>",
  "api_secret": "<API_SECRET>",
  "redirect_url": "http://127.0.0.1:8000/tradesmart/callback"
}
```

Response `200`:

```json
{
  "authorize_url": "https://v2api.tradesmartonline.in/OAuthlogin/authorize/oauth?client_id=<API_KEY>"
}
```

The `client_id` value is URL-encoded with `urllib.parse.urlencode`.

Errors:

| Status | `detail` | When |
| --- | --- | --- |
| 400 | `API key is required` | `api_key` is empty after trimming |
| 400 | `Redirect URL must be an http or https URL` | `redirect_url` has another scheme or no host |
| 400 | `API secret is required` | `api_secret` is empty or null and no secret is stored yet |
| 422 | validation list | `api_key` or `redirect_url` missing, or a field has the wrong type |

Side effects:

- Upserts `broker_config` row id 1: `api_key`, `redirect_url`, `updated_at`, and `api_secret_enc`
  (Fernet-encrypted) when a non-empty secret was sent.
- No broker call is made here.

---

## GET /tradesmart/callback

OAuth redirect target registered with TradeSmart. The browser arrives here after logging in on the
TradeSmart portal. Handled success and failure paths answer with a `302` redirect. Failures outside
the token-exchange try block (for example database or download-start failures) can return HTTP 500.

- Auth required: no

Query parameters:

| Name | Type | Notes |
| --- | --- | --- |
| `code` | string | Authorization code from TradeSmart. Trimmed. |
| `error_description`, `error`, `emsg` | string | Optional. When `code` is missing, the first non-empty value in this precedence order is appended to the error message. |

Redirect target base (`FRONTEND_URL`, from `config.frontend_url()`, evaluated per request):

- empty string (relative redirect) when `frontend/dist/index.html` exists, because the backend
  serves the UI itself;
- otherwise the value of the environment variable `SMARTTRADER_FRONTEND_URL` if set, else
  `http://127.0.0.1:5173` (the Vite dev server). A trailing slash is removed.

Success:

```
HTTP/1.1 302 Found
Location: http://127.0.0.1:5173/dashboard
Set-Cookie: st_session=<random>; HttpOnly; Path=/; SameSite=lax
```

(With a built frontend the `Location` is `/dashboard`.)

Failure:

```
HTTP/1.1 302 Found
Location: http://127.0.0.1:5173/login?error=TradeSmart%20login%20failed%3A%20Invalid%20code
```

The message is truncated to 300 characters and fully percent-encoded.

| Condition | `error` message |
| --- | --- |
| No `code` | `TradeSmart did not return an authorization code` (plus `: <broker message>` if `error_description`, `error` or `emsg` is present) |
| No config row, no API key, or the stored secret cannot be decrypted | `API key and secret are not configured. Save them and log in again.` |
| GenAcsTok answered with `stat` other than `Ok`, non-JSON, no token or no user id | `TradeSmart login failed: <broker emsg or reason>` |
| Network error talking to TradeSmart | `Could not reach TradeSmart. Please try again.` |
| Any other exception during token exchange | `Unexpected error during login` |

Processing on success:

1. Decrypts the stored API secret and calls TradeSmart `GenAcsTok` with
   `checksum = sha256_hex(api_key + api_secret + code)` (see
   [broker-integration.md](broker-integration.md#genacstok-token-exchange)).
2. Takes the access token from `access_token` (fallback `susertoken`), the user id from `actid`,
   `uid` or `USERID`, and the user name from `uname`.
3. Sets `is_active = false` on every active `auth_session` row.
4. Inserts a new `auth_session` row: `session_hash` (SHA-256 hex of the cookie value),
   `user_id`, `user_name`, `access_token_enc` (Fernet-encrypted), `created_at`, `is_active = true`.
5. Starts the master contract download in a background thread, unless one is already running (then
   it only logs).
6. Sets the `st_session` cookie (HttpOnly, SameSite=Lax, Path=/, not Secure, no Domain) and
   redirects to `<FRONTEND_URL>/dashboard`.

Other notes:

- The uvicorn access log filter in `main.py` replaces `code=<value>` with `code=[redacted]`.
  It matches literal query names only; encoded names such as `c%6fde` bypass the
  current regex. It is not a general secret-redaction mechanism.
- The saved redirect URL is not sent in the authorize or token-exchange request and is not compared
  against the callback URL. The broker uses its registered redirect configuration.
- No OAuth `state` is generated or validated; the callback does not require a prior local login request.

---

## GET /api/auth/session

Reports whether the browser has an active session. Used by the frontend route guard.

- Auth required: no (it reads the cookie if present)
- Request: none

Response `200` with a session:

```json
{
  "authenticated": true,
  "user_id": "<USER_ID>",
  "user_name": "<USER NAME>",
  "login_time": "2026-10-14T09:12:41+05:30"
}
```

Response `200` without a session (no cookie, unknown cookie, or deactivated session):

```json
{"authenticated": false, "user_id": null, "user_name": null, "login_time": null}
```

| Field | Type | Notes |
| --- | --- | --- |
| `authenticated` | boolean | |
| `user_id` | string or null | TradeSmart account id |
| `user_name` | string or null | From `uname` at login |
| `login_time` | string or null | Session `created_at` as IST ISO 8601 |

Errors: none. This endpoint never returns 401.
Side effects: none. It does not contact the broker. Broker-side expiry is detected only by a later
broker operation, not by a protected local-data endpoint such as symbol search.

---

## POST /api/auth/logout

Ends the current session.

- Auth required: no (works with or without a valid cookie)
- Request: none

Response `200`:

```json
{"status": "ok"}
```

The response always carries a `Set-Cookie` header that deletes `st_session` (Path=/, HttpOnly,
SameSite=Lax).

Errors: none.

Side effects:

- If the cookie maps to an active session, sets `auth_session.is_active = false`.
- No broker logout call is made. Existing tick connections and agent runs retain the credentials
  captured when they started; logout does not close or cancel them.

---

## GET /{path} (SPA fallback)

Registered last in `main.py`, so every API route wins. Not included in the OpenAPI schema.

- Auth required: no
- Applies to GET only.

Behaviour:

| Path | Result |
| --- | --- |
| `api`, `api/...`, `tradesmart/...` that matched no route | `404 {"detail": "Not Found"}` |
| `frontend/dist/index.html` does not exist | `404 {"detail": "Frontend not built. Run npm run build in frontend/."}` |
| A real file inside `frontend/dist` (path traversal is blocked by resolving and checking `is_relative_to`) | that file |
| Anything else | `frontend/dist/index.html` (client-side routing) |

The `dist` check runs per request, so a frontend build made after startup is picked up.

Every file it returns (`index.html` and the hashed assets) carries `Cache-Control: no-cache`
along with the `ETag` and `Last-Modified` that `FileResponse` sets. The browser revalidates on
each load and gets a 304 when the file is unchanged, so a chunk patched in place under the same
hashed name (see [../design/risks.md](../design/risks.md)) is picked up without a hard reload.

Side effects: none.

---

## Notes and possible issues

- The session cookie has no expiry and the server-side session never times out on its own. It ends on
  logout, on a new login, or when a funds, chart or orders call sees the broker report an expired
  token.
- `GET /api/auth/session` returns `authenticated: true` for a session whose stored token can no longer
  be decrypted (for example after `secret.key` changed). The next protected broker call then returns 401.
