# Getting started

## 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install randax-navimow-sdk
```

Or install a prebuilt wheel from the
[releases page](https://github.com/randax/navimow-sdk/releases). See the
top-level README for Raspberry Pi and `--user` variants.

## 2. Get an access token

The SDK does **not** perform OAuth2. It expects a bearer token that is already
valid for the Navimow OpenAPI. Obtain it through the platform's OAuth2 flow (or
from an existing integration) and hand it to the SDK as a string. When the
token is refreshed, call `client.update_token(new_token)`; nothing needs to be
rebuilt.

Two settings are required:

| Value | Meaning |
|---|---|
| `token` | Bearer token for the OpenAPI |
| `api_base_url` | Base URL of the OpenAPI, e.g. `https://<host>`; the SDK appends `/openapi/...` |

## 3. List your mowers

```python
import asyncio
import os

from mower_sdk import MowerClient, UrllibSession


async def main() -> None:
    async with UrllibSession() as session:
        client = MowerClient(
            session=session,
            token=os.environ["NAVIMOW_TOKEN"],
            api_base_url=os.environ["NAVIMOW_API_URL"],
        )
        for device in await client.async_discover_devices():
            print(f"{device.name:20} id={device.id} online={device.online}")


asyncio.run(main())
```

`UrllibSession` is the SDK's dependency-free HTTP transport. If you already
have an `aiohttp.ClientSession` (Python 3.10+), pass that instead — the SDK
only needs the `session.request(...)` context-manager shape.

## 4. Read status and send a command

```python
from mower_sdk import MowerAPIError, MowerStatus


async def mow_if_idle(client: MowerClient, device_id: str) -> None:
    status = await client.async_get_device_status(device_id)
    print(f"{status.status.value}, battery {status.battery}%")

    if status.status in (MowerStatus.IDLE, MowerStatus.DOCKED, MowerStatus.CHARGING):
        try:
            await client.async_start_mowing(device_id)
        except MowerAPIError as err:
            print("could not start:", err.error_code or err)
```

Available commands on `MowerClient`: `async_start_mowing`, `async_pause_mowing`,
`async_resume`, `async_dock` (each also has a blocking twin without the
`async_` prefix — see [event loops](event-loops.md) before using those).

## 5. Stream live state

REST status is a snapshot. For push updates use `NavimowSDK`, which connects
to the account's MQTT broker over WebSocket:

```python
from mower_sdk import DeviceStateMessage, NavimowSDK


async def watch(client: MowerClient) -> None:
    info = await client.async_refresh_mqtt_info()   # fetch broker + credentials
    sdk = NavimowSDK(
        broker=info["mqttHost"],
        port=443,
        username=info["userName"],
        password=info["pwdInfo"],
        ws_path=info["mqttUrl"],
        auth_headers={"Authorization": f"Bearer {client.get_token()}"},
    )
    sdk.on_state(lambda s: print(s.device_id, s.state, s.battery))
    sdk.connect()                       # binds to the running loop
    try:
        await asyncio.sleep(300)
    finally:
        sdk.disconnect()
```

The full version, with event and attribute callbacks and reconnect handling,
is in [Real-time updates](realtime.md) and `examples/watch_state.py`.
