# Errors and troubleshooting

## Exception types

| Exception | Raised by | Attributes |
|---|---|---|
| `MowerAPIError` | Every REST call | `message`, `status_code` (HTTP, may be `None`), `error_code` (platform code, may be `None`) |
| `MowerAuthError` | Reserved for auth flows | `message` |
| `MowerMQTTError` | `MowerMQTT` connect/subscribe | `message` |
| `HTTPClientError` | `UrllibSession` transport | wrapped into `MowerAPIError` by the API layer |
| `RuntimeError` | Loop misuse, `NavimowSDK` commands while disconnected | — |

Catch `MowerAPIError` for everything REST:

```python
from mower_sdk import MowerAPIError

try:
    await client.async_start_mowing(device_id)
except MowerAPIError as err:
    if err.status_code == 401:
        token = await refresh_token()          # your OAuth2 code
        client.update_token(token)
    elif err.error_code == "DEVICE_NOT_FOUND":
        ...
    else:
        log.warning("command failed: %s", err)   # "msg | HTTP 400 | Error Code: X"
```

`ERROR_MESSAGES` and `COMMAND_ERRORS` are lookup tables of platform messages
(in Chinese, as served upstream); the `error_code` string is the stable thing to
match on.

## Common problems

**`MowerAPIError: … TOKEN_EXPIRED` with `status_code=401` before any request**
— the token string is empty. Set it or call `update_token()`.

**`RuntimeError: NavimowSDK.connect() requires a running event loop or an explicit loop= argument`**
— you called `connect()` from plain synchronous code. Either run inside
`asyncio.run(...)` or pass `loop=` at construction.

**`RuntimeError: This SDK object is being used from a different event loop`**
— an object bound to one loop was touched from another (common with
`asyncio.run()` called twice). Create SDK objects inside the loop that will use
them, or pass `loop=` explicitly.

**`asyncio.run() cannot be called from a running event loop`**
— you used a blocking method (`discover_devices()`, `start_mowing()`, …) inside
async code. Use the `async_*` variant.

**No MQTT messages arrive**
— check, in order: `sdk.is_connected`; that `records=devices` was passed (so
subscriptions target real ids); that the token is still valid (the WebSocket
handshake carries it); and enable debug logging (below) to see topics and
payloads.

**Wildcard subscription warning** (`subscribing cloud topics with wildcard`)
— `records` was empty. Pass the device list.

## Logging

The SDK logs under the `mower_sdk` namespace. Connection details are logged at
INFO with secrets masked; raw payloads at DEBUG.

```python
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("mower_sdk.mqtt").setLevel(logging.DEBUG)
```
