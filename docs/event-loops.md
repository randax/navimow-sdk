# Home Assistant og andre hendingsløkker

SDK-en er asyncio-innfødd, men MQTT-tilbakekall oppstår på nettverkstråden til
paho. Forstår du korleis dei to møtest, unngår du dei vanlege `RuntimeError`-ane.

## Reglane

1. Eit SDK-objekt bind seg til **éi** asyncio-løkke for heile levetida: `loop=`
   du sender, eller den køyrande løkka ved første tilkopling.
2. Kvart MQTT-tilbakekall blir flytta over til den løkka med
   `call_soon_threadsafe(create_task, ...)`; `on_state` osb. køyrer **på løkka**,
   ikkje på MQTT-tråden.
3. Blokkerande hjelparar (`discover_devices()`, `start_mowing()`,
   `refresh_mqtt_info()`) kallar `asyncio.run()` og fungerer difor berre frå
   synkron kode utan køyrande løkke.
4. `disconnect()` før løkka blir stengd.

## Vanlege skript

```python
asyncio.run(main())        # lag og bruk alt inne i main()
```

Ikkje lag SDK-en ved modulimport og bruk han så inne i `asyncio.run` frå fleire
stader — kvart `asyncio.run` er ei ny løkke.

## Home Assistant

Lag klienten og SDK-en frå ein korutine som køyrer på `hass.loop` (t.d. i
`async_setup_entry`), så bind dei seg til henne automatisk, eller send
`loop=hass.loop` eksplisitt frå ein synkron kontekst:

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
        # Alt på hass.loop; trygt å røre entitetar / dispatcher her.
        async_dispatcher_send(hass, f"navimow_state_{msg.device_id}", msg)

    sdk.on_state(_on_state)
    sdk.connect()

    entry.async_on_unload(sdk.disconnect)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = (client, sdk)
    return True
```

Sidan tilbakekalla alt køyrer på `hass.loop`, treng du **ikkje**
`hass.add_job` / `call_soon_threadsafe` inne i dei. Hald dei korte; for I/O,
bruk `hass.async_create_task(coro)`.

Fornying av teikn i HA:

```python
async def _refresh(hass, client, sdk, oauth_session):
    await oauth_session.async_ensure_token_valid()
    token = oauth_session.token["access_token"]
    client.update_token(token)
    sdk.update_mqtt_credentials(auth_headers={"Authorization": f"Bearer {token}"})
```

## Køyr SDK-en på ein bakgrunnstråd

Er applikasjonen din synkron (t.d. eit Tk-grensesnitt), køyr éi løkke på ein
tråd og gje SDK-en den løkka:

```python
import asyncio, threading

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()

sdk = NavimowSDK(..., loop=loop)
loop.call_soon_threadsafe(sdk.connect)

# Frå hovudtråden:
fut = asyncio.run_coroutine_threadsafe(client.async_start_mowing(dev_id), loop)
fut.result(timeout=10)
```

## Merknader om Python-versjonar

- 3.9: `aiohttp` blir ikkje installert; bruk `UrllibSession`.
- 3.10–3.14: begge transportane fungerer; `aiohttp` er låst til ei lappa
  utgåve.
- 3.14 endra semantikken for eigarskap til hendingsløkka; «bind ved første
  tilkopling»-åtferda til SDK-en er utforma rundt det, og difor er eit eksplisitt
  `loop=` det tryggaste valet i langkøyrande tenester.
