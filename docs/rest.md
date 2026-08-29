# REST: oppdaging, status, kommandoar

`MowerClient` er inngangen til dagleg bruk. Han eig ein `MowerAPI` (REST) og
ein `MowerMQTT`, og held det gjeldande teiknet.

```python
from mower_sdk import MowerClient, UrllibSession

async with UrllibSession() as session:
    client = MowerClient(session=session, token=TOKEN, api_base_url=API_URL)
```

Parametrar til konstruktøren:

| Namn | Standard | Merknad |
|---|---|---|
| `session` | — | Alt som oppfyller `mower_sdk.http.HTTPSession` (`UrllibSession`, `aiohttp.ClientSession`) |
| `token` | — | Berarteikn |
| `api_base_url` | `""` | Grunn-URL; avsluttande skråstrek blir fjerna |
| `mqtt_broker`, `mqtt_port`, `mqtt_username`, `mqtt_password` | — | Valfrie; blir overskrivne av `async_refresh_mqtt_info()` |
| `loop` | `None` | Eksplisitt asyncio-løkke for MQTT-sida |
| `extra_topics` | `None` | Ekstra MQTT-emne som blir abonnerte ordrett |

Kvar metode nedanfor finst i asynkron form (`async_*`) og blokkerande form. Dei
blokkerande formene kallar `asyncio.run()` internt, så dei må **ikkje** kallast
frå ei køyrande løkke. Føretrekk dei asynkrone formene overalt, unnateke i
eingongsskript.

## MQTT-oppdateringar

`async_subscribe_device_updates()` og `subscribe_device_updates()` tek no
`callback=None`, `on_location=None` og `subscribe_location=False`. Når
`subscribe_location=True`, får `on_location` kvart godteke
`DeviceLocationMessage`; meldingar med stilleståande nullposisjon eller eldre
tidsstempel for same lesingstype blir filtrerte bort.

```python
await client.async_subscribe_device_updates(
    device_id,
    callback=handle_status,
    on_location=handle_location,
    subscribe_location=True,
)

location = client.get_cached_location(device_id)  # DeviceLocationMessage | None
```

## Oppdag einingar

```python
devices = await client.async_discover_devices()   # list[Device]
for d in devices:
    print(d.id, d.name, d.model, d.firmware_version, d.online)
```

`Device.extra` tek vare på råfelt som dataklassen ikkje modellerer.

## Les status

```python
status = await client.async_get_device_status(device_id)         # éi
statuses = await client.async_get_device_statuses([id1, id2])    # fleire, dict[id, DeviceStatus]
```

```python
print(status.status)          # MowerStatus.MOWING
print(status.battery)         # 87
print(status.error_code)      # MowerError.NONE
print(status.position)        # {"lat": 59.91, "lng": 10.75} eller None
```

Ein ukjend ID kastar `MowerAPIError` med `error_code="DEVICE_NOT_FOUND"` og
`status_code=404`.

## Send kommandoar

```python
await client.async_start_mowing(device_id)
await client.async_pause_mowing(device_id)
await client.async_resume(device_id)
await client.async_dock(device_id)
```

Eller gå via API-et for `STOP`:

```python
from mower_sdk import MowerCommand
await client.api.async_send_command(device_id, MowerCommand.STOP)
```

Kvar returnerer `data`-ordboka frå tenaren. Ein avvist kommando kastar
`MowerAPIError` der `error_code` er koden frå plattforma (t.d.
`deviceOffline`). `alreadyInState` blir svelgd og rekna som vellykka.

For å hente utfallet av tidlegare kommandoar:

```python
results = await client.api.async_query_command_results(
    [{"id": device_id, "cmdNum": "…"}]
)
```

## Byt teikn

```python
client.update_token(new_token)
```

Dette oppdaterer REST-hovudet med ein gong. Har du òg ein `NavimowSDK`, kall
`sdk.update_mqtt_credentials(auth_headers={"Authorization": f"Bearer {new_token}"})`
slik at WebSocket-handtrykket brukar det nye teiknet ved neste attkopling.

## Bruk aiohttp i staden for UrllibSession

```python
import aiohttp

async with aiohttp.ClientSession() as session:
    client = MowerClient(session=session, token=TOKEN, api_base_url=API_URL)
```

Begge transportane oppfyller same protokoll; `UrllibSession` køyrer kvar
førespurnad i ein arbeidstråd via `asyncio.to_thread`, har 30 s tidsavbrot og
16 MiB svargrense (`UrllibSession(timeout=10, max_response_bytes=1_000_000)`),
og fjernar legitimasjon ved omdirigering til andre opphav.

## Fullt døme: ein liten CLI

Sjå `examples/control.py`:

```bash
python examples/control.py list
python examples/control.py status <device_id>
python examples/control.py start|pause|resume|dock <device_id>
```
