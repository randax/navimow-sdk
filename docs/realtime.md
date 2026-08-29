# Real-time updates over MQTT

`NavimowSDK` gives you push updates for state, events and attributes, plus
per-device caches of the last state and attributes.

## Obtaining broker credentials

The MQTT broker, WebSocket path and per-account credentials are served by the
REST API. Fetch them with `MowerClient`:

```python
info = await client.async_refresh_mqtt_info()
# info == {"mqttHost": "...", "mqttUrl": "/mqtt", "userName": "...", "pwdInfo": "...", ...}
```

`async_refresh_mqtt_info()` also stores the values on the client
(`client.mqtt_broker`, `client.mqtt_ws_path`, `client.mqtt_username`,
`client.mqtt_password`) so you can build the SDK from either source.

## Connecting

```python
from mower_sdk import NavimowSDK

sdk = NavimowSDK(
    broker=client.mqtt_broker,
    port=client.mqtt_port,               # 443 after refresh
    username=client.mqtt_username,
    password=client.mqtt_password,
    ws_path=client.mqtt_ws_path,         # enables WebSocket + TLS
    auth_headers={"Authorization": f"Bearer {client.get_token()}"},
    records=devices,                     # list[Device]; subscribes per id
)
sdk.connect()        # non-blocking; needs a running loop or loop=
...
sdk.disconnect()
```

Pass `records=devices` so the client subscribes to exact per-device topics.
Without it the client subscribes with the `+` wildcard, which some brokers
reject.

Tuning knobs: `keepalive_seconds=2400`, `reconnect_min_delay=1`,
`reconnect_max_delay=60`.

## Callbacks

```python
from mower_sdk import DeviceAttributesMessage, DeviceEventMessage, DeviceStateMessage

def on_state(msg: DeviceStateMessage) -> None:
    print(f"[{msg.device_id}] {msg.state} battery={msg.battery}% pos={msg.position}")

def on_event(msg: DeviceEventMessage) -> None:
    print(f"[{msg.device_id}] {msg.type}/{msg.event} level={msg.level} {msg.message or ''}")

def on_attributes(msg: DeviceAttributesMessage) -> None:
    print(f"[{msg.device_id}] attributes: {msg.attributes}")

sdk.on_state(on_state)
sdk.on_event(on_event)
sdk.on_attributes(on_attributes)
```

Callbacks are **synchronous** and run as tasks on the SDK's asyncio loop
(paho's network thread hands them over with `call_soon_threadsafe`). Keep them
quick; to await something, schedule a task:

```python
def on_state(msg):
    asyncio.get_running_loop().create_task(handle_state(msg))
```

Register callbacks before `connect()` so the first retained/initial message is
not missed.

## Caches

```python
sdk.get_cached_state(device_id)        # DeviceStateMessage | None
sdk.get_cached_attributes(device_id)   # DeviceAttributesMessage | None
sdk.is_connected                       # bool
```

## Credential refresh and reconnects

Paho reconnects automatically with exponential backoff. If the account token
or MQTT password changes, update in place — the client is rebuilt if needed and
reconnects with the new values:

```python
sdk.update_mqtt_credentials(
    username=info["userName"],
    password=info["pwdInfo"],
    auth_headers={"Authorization": f"Bearer {new_token}"},
)
```

This is safe to call before an event loop exists; the reconnect is deferred
until `connect()`.

## Complete example

`examples/watch_state.py` puts it together: discover devices, fetch MQTT info,
subscribe, print updates until Ctrl-C, disconnect cleanly.

```python
import asyncio, os, signal
from mower_sdk import MowerClient, NavimowSDK, UrllibSession


async def main() -> None:
    async with UrllibSession() as session:
        client = MowerClient(
            session=session,
            token=os.environ["NAVIMOW_TOKEN"],
            api_base_url=os.environ["NAVIMOW_API_URL"],
        )
        devices = await client.async_discover_devices()
        await client.async_refresh_mqtt_info()

        sdk = NavimowSDK(
            broker=client.mqtt_broker, port=client.mqtt_port,
            username=client.mqtt_username, password=client.mqtt_password,
            ws_path=client.mqtt_ws_path,
            auth_headers={"Authorization": f"Bearer {client.get_token()}"},
            records=devices,
        )
        sdk.on_state(lambda m: print("state", m.device_id, m.state, m.battery))
        sdk.on_event(lambda m: print("event", m.device_id, m.event, m.message))

        stop = asyncio.Event()
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
        sdk.connect()
        try:
            await stop.wait()
        finally:
            sdk.disconnect()


asyncio.run(main())
```

## Device objects (`Navimow`, `NavimowCloudDevice`, `StateManager`)

For applications that want one object per mower with its own subscribers:

```python
from mower_sdk import Navimow

account = Navimow(client)
cloud = await account.initiate_cloud_connection(devices)   # builds NavimowMQTT + NavimowCloud
mowers = account.add_devices(devices)                      # list[NavimowCloudDevice]

async def on_state(state): print(state.state)
mowers[0].state_manager.state_callback.add_subscribers(on_state)
```

`StateManager` keeps `last_state`, `last_attributes`, `last_event` and exposes
`DataEvent` hooks (`state_callback`, `event_callback`, `attributes_callback`).
Subscribers are held by weak reference, so keep a strong reference to your
handler (module-level function or bound method on a live object).

> Note: `NavimowCloud` currently parses `navimow/{id}/{channel}` topics while
> the broker publishes `/downlink/vehicle/{id}/realtimeDate/{channel}`, so this
> layer does not yet receive live messages end-to-end. Use `NavimowSDK` for
> production real-time data.
