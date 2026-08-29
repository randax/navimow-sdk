# REST: discovery, status, commands

`MowerClient` is the everyday entry point. It owns a `MowerAPI` (REST) and a
`MowerMQTT` (placeholder) and keeps the current token.

```python
from mower_sdk import MowerClient, UrllibSession

async with UrllibSession() as session:
    client = MowerClient(session=session, token=TOKEN, api_base_url=API_URL)
```

Constructor parameters:

| Name | Default | Notes |
|---|---|---|
| `session` | — | Anything matching `mower_sdk.http.HTTPSession` (`UrllibSession`, `aiohttp.ClientSession`) |
| `token` | — | Bearer token |
| `api_base_url` | `""` | Base URL; trailing slash is stripped |
| `mqtt_broker`, `mqtt_port`, `mqtt_username`, `mqtt_password` | — | Optional; overwritten by `async_refresh_mqtt_info()` |
| `loop` | `None` | Explicit asyncio loop for the MQTT side |

Every method below has an async form (`async_*`) and a blocking form. The
blocking forms call `asyncio.run()` internally, so they must **not** be called
from inside a running loop. Prefer the async forms everywhere except one-off
scripts.

## Discover devices

```python
devices = await client.async_discover_devices()   # list[Device]
for d in devices:
    print(d.id, d.name, d.model, d.firmware_version, d.online)
```

`Device.extra` keeps the raw payload fields the dataclass does not model.

## Read status

```python
status = await client.async_get_device_status(device_id)         # one
statuses = await client.async_get_device_statuses([id1, id2])    # many, dict[id, DeviceStatus]
```

```python
print(status.status)          # MowerStatus.MOWING
print(status.battery)         # 87
print(status.error_code)      # MowerError.NONE
print(status.position)        # {"lat": 59.91, "lng": 10.75} or None
```

An unknown id raises `MowerAPIError` with `error_code="DEVICE_NOT_FOUND"` and
`status_code=404`.

## Send commands

```python
await client.async_start_mowing(device_id)
await client.async_pause_mowing(device_id)
await client.async_resume(device_id)
await client.async_dock(device_id)
```

Or go through the API for `STOP`:

```python
from mower_sdk import MowerCommand
await client.api.async_send_command(device_id, MowerCommand.STOP)
```

Each returns the server's `data` dict. A rejected command raises
`MowerAPIError` whose `error_code` is the platform's code (e.g.
`deviceOffline`). `alreadyInState` is swallowed and treated as success.

To poll for the outcome of earlier commands:

```python
results = await client.api.async_query_command_results(
    [{"id": device_id, "cmdNum": "…"}]
)
```

## Rotate the token

```python
client.update_token(new_token)
```

This updates the REST header immediately. If you also hold a `NavimowSDK`,
call `sdk.update_mqtt_credentials(auth_headers={"Authorization": f"Bearer {new_token}"})`
so the WebSocket handshake uses the new token on the next reconnect.

## Using aiohttp instead of UrllibSession

```python
import aiohttp

async with aiohttp.ClientSession() as session:
    client = MowerClient(session=session, token=TOKEN, api_base_url=API_URL)
```

Both transports satisfy the same protocol; `UrllibSession` runs each request in
a worker thread via `asyncio.to_thread`, has a 30 s timeout and a 16 MiB
response cap (`UrllibSession(timeout=10, max_response_bytes=1_000_000)`), and
strips credentials on cross-origin redirects.

## Full example: a small CLI

See `examples/control.py`:

```bash
python examples/control.py list
python examples/control.py status <device_id>
python examples/control.py start|pause|resume|dock <device_id>
```
