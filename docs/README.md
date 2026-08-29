# Navimow SDK documentation

`randax-navimow-sdk` (imported as `mower_sdk`) talks to the Navimow cloud on
behalf of a Navimow account: it lists mowers, reads their status, sends
commands over REST, and streams live state over MQTT.

| Guide | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, obtain a token, list mowers, send a command |
| [How it works](architecture.md) | Layers, transports, topics, and event-loop rules |
| [REST: discovery, status, commands](rest.md) | `MowerClient` / `MowerAPI` in depth |
| [Real-time updates over MQTT](realtime.md) | `NavimowSDK`, callbacks, caches, reconnects |
| [Data models](models.md) | `Device`, `DeviceStatus`, MQTT message dataclasses, enums |
| [Errors and troubleshooting](errors.md) | Exception types, common failures, logging |
| [Home Assistant and other event loops](event-loops.md) | Loop ownership, thread-safety, HA patterns |

Runnable scripts live in [`../examples`](../examples). They read
`NAVIMOW_TOKEN` and `NAVIMOW_API_URL` from the environment so you can try
them without editing code:

```bash
export NAVIMOW_TOKEN="eyJ..."
export NAVIMOW_API_URL="https://<navimow-openapi-host>"
python examples/list_devices.py
```
