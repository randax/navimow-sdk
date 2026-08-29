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

The distribution is named `randax-navimow-sdk` on PyPI and is imported as
`mower_sdk` in Python. Use standard, GIL-enabled CPython 3.9.2 through 3.14.x.

### Recommended installation

The SDK does not require a virtual environment, but one is recommended to keep
its dependencies isolated from other Python applications:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install randax-navimow-sdk
python -c "import mower_sdk; print(mower_sdk.__version__)"
```

On Windows PowerShell, activate the environment with
`.venv\Scripts\Activate.ps1`. To upgrade an existing installation, run:

```bash
python -m pip install --upgrade randax-navimow-sdk
```

### Install for your user without a virtual environment

Install the SDK into the current user's Python package directory with:

```bash
python3 -m pip install --user --upgrade pip
python3 -m pip install --user --upgrade randax-navimow-sdk
python3 -c "import mower_sdk; print(mower_sdk.__version__)"
```

This does not require root access and does not modify packages owned by other
users. If Python reports that the environment is externally managed, use the
recommended virtual-environment installation instead. For a dedicated Python
installation or container, a plain `python3 -m pip install randax-navimow-sdk`
is also supported. Do not use `sudo pip`.

### Raspberry Pi OS

Raspberry Pi OS users may need to install virtual-environment support first:

```bash
sudo apt update
sudo apt install python3-venv
```

Then use either the virtual-environment installation or the per-user
`--user` installation above. Required runtime dependencies are installed
automatically.

#### Python 3.9 HTTP transport

Current aiohttp security releases no longer support Python 3.9, so Python 3.9
installations use the SDK's dependency-free `UrllibSession`. Python 3.10 and
newer also install a patched aiohttp release for compatibility with existing
applications that inject an `aiohttp.ClientSession`. Both session types satisfy
the same SDK transport interface.

### Install from source

From a source checkout, install the current revision with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Without a virtual environment, install the source checkout for the current user
with `python3 -m pip install --user .`.

For development, include the formatting, type-checking, build, and test tools:

```bash
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
```

## Quick Example

```python
import asyncio

from mower_sdk import MowerClient, UrllibSession


async def main() -> None:
    async with UrllibSession() as session:
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
