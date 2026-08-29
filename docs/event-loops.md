# Home Assistant and other event loops

The SDK is asyncio-native but MQTT callbacks originate on paho's network
thread. Understanding how the two meet avoids the common `RuntimeError`s.

## The rules

1. An SDK object binds to **one** asyncio loop for its lifetime: the `loop=`
   you pass, or the running loop at the first connect.
2. Every MQTT callback is hopped onto that loop with
   `call_soon_threadsafe(create_task, ...)`; your `on_state` etc. run **on the
   loop**, not on the MQTT thread.
3. Blocking helpers (`discover_devices()`, `start_mowing()`, `refresh_mqtt_info()`)
   call `asyncio.run()` and therefore only work from synchronous code with no
   loop running.
4. `disconnect()` before the loop closes.

## Plain scripts

```python
asyncio.run(main())        # create and use everything inside main()
```

Do not create the SDK at module import time and then use it inside
`asyncio.run` from several places — each `asyncio.run` is a new loop.

## Home Assistant

Create the client and SDK from a coroutine running on `hass.loop` (e.g. in
`async_setup_entry`) so they bind to it automatically, or pass
`loop=hass.loop` explicitly from a sync context:

```python
from homeassistant.core import HomeAssistant, callback
from mower_sdk import MowerClient, NavimowSDK
from homeassistant.helpers.aiohttp_client import async_get_clientsession


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    session = async_get_clientsession(hass)
    client = MowerClient(session=session, token=entry.data["token"],
                         api_base_url=entry.data["api_url"], loop=hass.loop)

    devices = await client.async_discover_devices()
    await client.async_refresh_mqtt_info()

    sdk = NavimowSDK(
        broker=client.mqtt_broker, port=client.mqtt_port,
        username=client.mqtt_username, password=client.mqtt_password,
        ws_path=client.mqtt_ws_path,
        auth_headers={"Authorization": f"Bearer {client.get_token()}"},
        records=devices, loop=hass.loop,
    )

    @callback
    def _on_state(msg):
        # Already on hass.loop; safe to touch entities / dispatcher here.
        async_dispatcher_send(hass, f"navimow_state_{msg.device_id}", msg)

    sdk.on_state(_on_state)
    sdk.connect()

    entry.async_on_unload(sdk.disconnect)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = (client, sdk)
    return True
```

Because callbacks already run on `hass.loop`, you do **not** need
`hass.add_job` / `call_soon_threadsafe` inside them. Keep them short; for I/O,
`hass.async_create_task(coro)`.

Token refresh in HA:

```python
async def _refresh(hass, client, sdk, oauth_session):
    await oauth_session.async_ensure_token_valid()
    token = oauth_session.token["access_token"]
    client.update_token(token)
    sdk.update_mqtt_credentials(auth_headers={"Authorization": f"Bearer {token}"})
```

## Running the SDK on a background thread

If your application is synchronous (e.g. a Tk GUI), run one loop on a thread
and hand the SDK that loop:

```python
import asyncio, threading

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()

sdk = NavimowSDK(..., loop=loop)
loop.call_soon_threadsafe(sdk.connect)

# From the main thread:
fut = asyncio.run_coroutine_threadsafe(client.async_start_mowing(dev_id), loop)
fut.result(timeout=10)
```

## Python version notes

- 3.9: `aiohttp` is not installed; use `UrllibSession`.
- 3.10–3.14: either transport works; `aiohttp` is pinned to a patched release.
- 3.14 changed event-loop ownership semantics; the SDK's "bind at first
  connect" behaviour is designed around that, which is why an explicit `loop=`
  is the safest choice in long-running services.
