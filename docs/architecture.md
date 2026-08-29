# How the SDK works

## Layers

```
┌──────────────────────────────────────────────────────────────┐
│  Your application / Home Assistant integration               │
├──────────────────────────────────────────────────────────────┤
│  MowerClient          NavimowSDK            Navimow          │
│  (REST facade +       (MQTT facade with     (account +       │
│   token holder)        typed callbacks)      device objects) │
├───────────────┬──────────────────────┬───────────────────────┤
│  MowerAPI     │  NavimowMQTT         │  NavimowCloud         │
│  REST calls   │  paho-mqtt over WSS  │  NavimowCloudDevice   │
│               │                      │  StateManager         │
├───────────────┼──────────────────────┴───────────────────────┤
│  HTTPSession  │  paho-mqtt Client (own network thread)       │
│  (Urllib /    │                                              │
│   aiohttp)    │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

Pick the entry point that matches the job:

| You want to… | Use |
|---|---|
| List mowers, read a status snapshot, start/pause/resume/dock | `MowerClient` (or `MowerAPI` directly) |
| Receive state/event/attribute pushes as they happen | `NavimowSDK` |
| Model each mower as an object with its own last-known state and subscribers | `Navimow` → `NavimowCloudDevice` → `StateManager` |
| Bring your own HTTP client | Any object matching the `HTTPSession` protocol in `mower_sdk.http` |

## REST transport

`MowerAPI` builds every request the same way:

1. `Authorization: Bearer <token>` and a fresh `requestId` UUID header.
2. `session.request(method, url, json=..., params=..., headers=...)` used as an
   async context manager.
3. HTTP ≥ 400 → `MowerAPIError(status_code=...)`.
4. Body is JSON with a `code` field; anything other than `code == 1` →
   `MowerAPIError` carrying the server's `desc`.
5. Transport failures (`HTTPClientError`, `aiohttp.ClientError`) are wrapped in
   `MowerAPIError` so callers only catch one type.

Endpoints used:

| Method | Path | Purpose |
|---|---|---|
| GET | `/openapi/smarthome/authList` | List authorised devices |
| POST | `/openapi/smarthome/getVehicleStatus` | Batch status for device ids |
| POST | `/openapi/smarthome/sendCommands` | Send a Google-Smart-Home-style command |
| POST | `/openapi/smarthome/responseCommands` | Poll results of earlier commands |
| GET | `/openapi/mqtt/userInfo/get/v2` | Fetch MQTT host, WebSocket path and credentials |

`MowerCommand` values are translated to the platform's command vocabulary:

| `MowerCommand` | Wire command | params |
|---|---|---|
| `START` | `action.devices.commands.StartStop` | `{"on": true}` |
| `STOP` | `action.devices.commands.StartStop` | `{"on": false}` |
| `PAUSE` | `action.devices.commands.PauseUnpause` | `{"on": false}` |
| `RESUME` | `action.devices.commands.PauseUnpause` | `{"on": true}` |
| `DOCK` | `action.devices.commands.Dock` | — |

A command result of `alreadyInState` is treated as success, so "start" on a
mower that is already mowing does not raise.

## MQTT transport

`NavimowMQTT` wraps `paho-mqtt` (callback API v2). The connection details come
from `/openapi/mqtt/userInfo/get/v2`; `MowerClient.async_refresh_mqtt_info()`
fetches them and stores `mqtt_broker`, `mqtt_username`, `mqtt_password` and
`mqtt_ws_path` on the client.

- Transport is **WebSocket + TLS** whenever a `ws_path` is given (which the
  cloud always provides); port 443.
- The client id is derived from the username, so one account can hold several
  connections without clashing.
- Keepalive defaults to 2400 s; automatic reconnect backs off from 1 s to 60 s.
- On every (re)connect the client subscribes to, per device id:

  ```
  /downlink/vehicle/{device_id}/realtimeDate/state
  /downlink/vehicle/{device_id}/realtimeDate/event
  /downlink/vehicle/{device_id}/realtimeDate/attributes
  ```

  With no device ids it falls back to the `+` wildcard.

- Incoming payloads are JSON; the device id from the topic is injected as
  `device_id` before parsing into `DeviceStateMessage`, `DeviceEventMessage`
  or `DeviceAttributesMessage`.

Paho runs its network loop in **its own thread**. Every callback that reaches
your code is hopped onto the asyncio loop the SDK is bound to via
`loop.call_soon_threadsafe(asyncio.create_task, coro)`. That is why the loop
rules below matter.

## Event-loop ownership

- Pass `loop=` to bind an SDK object to a specific loop for its lifetime.
- Otherwise the object binds to the **running** loop the first time connection
  work happens (`connect()`, `async_connect()`, `connect_async()`).
- Using an object from a different loop later raises
  `RuntimeError("This SDK object is being used from a different event loop")`.
- A closed loop is rejected up front.
- Always `disconnect()` before closing the loop; otherwise MQTT callbacks are
  dropped with a debug log.

See [Home Assistant and other event loops](event-loops.md) for patterns.

## Placeholder pieces

Two parts of the codebase are still stubs and are documented for completeness
only:

- `MowerMQTT` (used by `MowerClient.subscribe_device_updates`) subscribes to
  `device/{id}/status` — a TODO topic scheme. Use `NavimowSDK` for live data.
- `NavimowCloud` parses topics of the form `navimow/{id}/{channel}`, whereas the
  broker publishes `/downlink/vehicle/...`. The `Navimow` / `NavimowCloudDevice`
  object model therefore does not receive live messages from the real broker
  yet; it works with `NavimowSDK` caches or your own dispatch.
- `NavimowSDK.start_mowing()` etc. publish to `navimow/{id}/command`. For
  reliable control use the REST commands on `MowerClient`.
