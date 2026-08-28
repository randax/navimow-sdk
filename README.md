# Navimow Python SDK

<p align="center">
  <img src="https://fra-navimow-prod.s3.eu-central-1.amazonaws.com/img/navimowhomeassistant.png" width="600">
</p>

A lightweight Python SDK for integrating Navimow robotic mowers with cloud platforms and smart home systems.

It provides a simple interface for device discovery, status monitoring, and mower control using REST APIs and MQTT-based real-time communication.

Supported Python versions: standard, GIL-enabled CPython 3.9.2 through 3.14.x.
Raspberry Pi OS on ARM32 and ARM64 is a first-class target.

Loop behavior: if you pass an event loop, the SDK uses that loop for its lifetime; otherwise it binds to the current running loop when connection work begins. SDK objects can be created from synchronous code, but synchronous connection entry points require either an explicit loop or a running loop. Disconnect SDK objects before closing their loop.

## Features

- REST API client for device management
- MQTT-based real-time status updates
- Device discovery
- Mower control (start, pause, resume, dock)
- Sync and async interfaces
- Designed for Home Assistant integrations

More features are being added over time.

## Installation

Install from PyPI:

```bash
pip install navimow-sdk
```

## Quick Example

```python
import asyncio

import aiohttp

from mower_sdk import MowerClient


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = MowerClient(
            session=session,
            token="your_access_token",
            api_base_url="https://api.example.com",
            mqtt_broker="mqtt.example.com",
        )

        devices = await client.async_discover_devices()
        print(devices)

        await client.async_start_mowing("device_id")


if __name__ == "__main__":
    asyncio.run(main())
```

> The SDK does not handle OAuth2 authentication. You must obtain the access token separately.

## Compatibility verification

GitHub Actions runs the offline regression suite on CPython 3.9.2 and every minor
version through 3.14. The endpoint versions also run under ARM32 and ARM64
emulation, including installation of the package and its runtime dependencies.

Before a release, run the same non-destructive smoke test on a physical Raspberry
Pi. It installs the package, imports the public API, and exercises the offline
asyncio and MQTT lifecycle tests without contacting a mower, cloud account, or
public broker:

```bash
python -m pip install .
python -c "import mower_sdk; print(mower_sdk.__version__)"
python -m unittest discover -s tests -v
```

Live mower and cloud tests are optional and must remain credential-gated.

## Core Capabilities

* **Device Discovery** – Retrieve mower devices linked to an account
* **Device Status** – Get current mower state and battery level
* **Real-time Updates** – Receive MQTT status updates
* **Device Control** – Start, pause, resume mowing or return to dock

Typical mower states include:

* `idle`
* `mowing`
* `paused`
* `docked`
* `charging`
* `returning`
* `error`

## Contributing

Issues and Pull Requests are welcome.

Please write any new human-authored comments and docstrings in neutral modern Nynorsk.
Keep runtime strings, identifiers, protocol fields, commands, URLs, and log messages unchanged.

## License

GPL-3.0-only
