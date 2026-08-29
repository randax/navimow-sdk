# Kom i gang

## 1. Installer

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install randax-navimow-sdk
```

Eller installer eit ferdigbygd hjul frå
[utgjevingssida](https://github.com/randax/navimow-sdk/releases). Sjå README på
toppnivå for Raspberry Pi- og `--user`-variantar.

## 2. Skaff eit tilgangsteikn

SDK-en utfører **ikkje** OAuth2. Han ventar eit berarteikn (bearer token) som
alt er gyldig for Navimow OpenAPI. Skaff det gjennom OAuth2-flyten til
plattforma (eller frå ei eksisterande integrasjon) og gje det til SDK-en som ein
streng. Når teiknet blir fornya, kall `client.update_token(nytt_teikn)`; ingenting
treng byggjast opp att.

To innstillingar er påkravde:

| Verdi | Tyding |
|---|---|
| `token` | Berarteikn for OpenAPI |
| `api_base_url` | Grunn-URL for OpenAPI, t.d. `https://<vert>`; SDK-en legg til `/openapi/...` |

## 3. List klipparane dine

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

`UrllibSession` er den avhengnadsfrie HTTP-transporten til SDK-en. Har du alt
ein `aiohttp.ClientSession` (Python 3.10+), kan du sende den i staden — SDK-en
treng berre `session.request(...)`-forma med kontekstbehandlar.

## 4. Les status og send ein kommando

```python
from mower_sdk import MowerAPIError, MowerStatus


async def mow_if_idle(client: MowerClient, device_id: str) -> None:
    status = await client.async_get_device_status(device_id)
    print(f"{status.status.value}, batteri {status.battery}%")

    if status.status in (MowerStatus.IDLE, MowerStatus.DOCKED, MowerStatus.CHARGING):
        try:
            await client.async_start_mowing(device_id)
        except MowerAPIError as err:
            print("kunne ikkje starte:", err.error_code or err)
```

Tilgjengelege kommandoar på `MowerClient`: `async_start_mowing`,
`async_pause_mowing`, `async_resume`, `async_dock` (kvar har òg ein blokkerande
tvilling utan `async_`-prefikset — les [hendingsløkker](event-loops.md) før du
brukar dei).

## 5. Strøym sanntidstilstand

REST-status er eit augneblinksbilete. For push-oppdateringar brukar du
`NavimowSDK`, som koplar seg til MQTT-meglaren til kontoen over WebSocket:

```python
from mower_sdk import DeviceStateMessage, NavimowSDK


async def watch(client: MowerClient) -> None:
    info = await client.async_refresh_mqtt_info()   # hent meglar + legitimasjon
    sdk = NavimowSDK(
        broker=info["mqttHost"],
        port=443,
        username=info["userName"],
        password=info["pwdInfo"],
        ws_path=info["mqttUrl"],
        auth_headers={"Authorization": f"Bearer {client.get_token()}"},
    )
    sdk.on_state(lambda s: print(s.device_id, s.state, s.battery))
    sdk.connect()                       # bind til den køyrande løkka
    try:
        await asyncio.sleep(300)
    finally:
        sdk.disconnect()
```

Den fulle versjonen, med hendings- og attributt-tilbakekall og handtering av
attkopling, finn du i [Sanntidsoppdateringar](realtime.md) og
`examples/watch_state.py`.
