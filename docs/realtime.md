# Sanntidsoppdateringar over MQTT

`NavimowSDK` gjev deg push-oppdateringar for tilstand, hendingar og attributtar,
pluss mellomlager per eining med siste tilstand og attributtar. Posisjon er eit
eige, frivillig abonnement.

## Skaff meglarlegitimasjon

MQTT-meglaren, WebSocket-stien og legitimasjonen per konto blir serverte av
REST-API-et. Hent dei med `MowerClient`:

```python
info = await client.async_refresh_mqtt_info()
# info == {"mqttHost": "...", "mqttUrl": "/mqtt", "userName": "...", "pwdInfo": "...", ...}
```

`async_refresh_mqtt_info()` lagrar òg verdiane på klienten
(`client.mqtt_broker`, `client.mqtt_ws_path`, `client.mqtt_username`,
`client.mqtt_password`), så du kan byggje SDK-en frå kva som helst av kjeldene.

## Kople til

```python
from mower_sdk import NavimowSDK

sdk = NavimowSDK(
    broker=client.mqtt_broker,
    port=client.mqtt_port,               # 443 etter oppfrisking
    username=client.mqtt_username,
    password=client.mqtt_password,
    ws_path=client.mqtt_ws_path,         # slår på WebSocket + TLS
    auth_headers={"Authorization": f"Bearer {client.get_token()}"},
    records=devices,                     # list[Device]; abonnerer per ID
)
sdk.connect()        # ikkje-blokkerande; treng ei køyrande løkke eller loop=
...
sdk.disconnect()
```

Send `records=devices` slik at klienten abonnerer på eksakte emne per eining.
Utan det abonnerer klienten med jokerteiknet `+`, som somme meglarar avviser.

Justeringsknappar: `keepalive_seconds=2400`, `reconnect_min_delay=1`,
`reconnect_max_delay=60`.

## Tilbakekall

```python
from mower_sdk import DeviceAttributesMessage, DeviceEventMessage, DeviceStateMessage

def on_state(msg: DeviceStateMessage) -> None:
    print(f"[{msg.device_id}] {msg.state} batteri={msg.battery}% pos={msg.position}")

def on_event(msg: DeviceEventMessage) -> None:
    print(f"[{msg.device_id}] {msg.type}/{msg.event} nivå={msg.level} {msg.message or ''}")

def on_attributes(msg: DeviceAttributesMessage) -> None:
    print(f"[{msg.device_id}] attributtar: {msg.attributes}")

sdk.on_state(on_state)
sdk.on_event(on_event)
sdk.on_attributes(on_attributes)
```

Tilbakekalla er **synkrone** og køyrer som oppgåver på asyncio-løkka til
SDK-en (nettverkstråden til paho leverer dei med `call_soon_threadsafe`). Hald
dei korte; treng du å vente på noko, planlegg ei oppgåve:

```python
def on_state(msg):
    asyncio.get_running_loop().create_task(handle_state(msg))
```

Registrer tilbakekall før `connect()` slik at du ikkje går glipp av den første
meldinga.

## Posisjon (`location`-kanalen)

Set `subscribe_location=True` for å abonnere på
`/downlink/vehicle/{device_id}/realtimeDate/location`. Lasta kan innehalde éin
eller fleire punkt; `on_location()` blir kalla éin gong for kvart godteke punkt,
i same rekkjefølgje som det kom.

```python
from mower_sdk import DeviceLocationMessage

sdk = NavimowSDK(..., records=devices, subscribe_location=True)

def on_location(point: DeviceLocationMessage) -> None:
    print(point.device_id, point.x, point.y, point.theta, point.mowing_percentage)

sdk.on_location(on_location)
```

`x` og `y` er meter relativt til ladestasjonen, og `theta` er radianar.
`get_cached_location(device_id)` gjev sist godtekne `DeviceLocationMessage`.
Ein nulltrippel (`x`, `y` og `theta` alle nøyaktig `0.0`) er ein plasshaldar frå
ein stilleståande klippar og blir kasta. Sidan meglaren kan forseinke eller
omorganisere meldingar, blir eldre punkt kasta per `(device_id, type)` når dei
har tidsstempel. Punkt utan `timestamp` eller `type` går alltid gjennom.

Felta og leidningsforma er observerte gjennom tredjepartsadapteren
`ioBroker.navimow`, ikkje ei offisiell spesifikasjon.

## Ekstra emne og rå meldingar

`extra_topics` abonnerer på kvart oppgjeve MQTT-emne ordrett. `on_raw()` får
`(topic, bytes)` for **kvar** mottatt melding, også ukjende emne og meldingar
utan kjend einings-ID. På `NavimowSDK` blir råtilbakekall, som andre
SDK-tilbakekall, køyrde på den bundne asyncio-løkka. På `MowerMQTT` er `on_raw`
eit synkront tilbakekall som blir planlagt på løkka når ei finst, elles kalla
direkte frå MQTT-tråden. Merk at `extra_topics` som overlappar dei innebygde
emna (t.d. `/downlink/vehicle/{id}/#`) gjer at meglaren leverer kvar melding
éin gong per treffande abonnement, så `--discover` i dømeskriptet gjev doble
utskrifter.

```python
sdk = NavimowSDK(..., extra_topics=["/downlink/vehicle/device-1/#"])
sdk.on_raw(lambda topic, payload: print(topic, payload.decode("utf-8", "replace")))
```

## Mellomlager

```python
sdk.get_cached_state(device_id)        # DeviceStateMessage | None
sdk.get_cached_attributes(device_id)   # DeviceAttributesMessage | None
sdk.get_cached_location(device_id)     # DeviceLocationMessage | None
sdk.is_connected                       # bool
```

## Fornying av legitimasjon og attkopling

Paho koplar til att automatisk med eksponentiell venting. Endrar kontoteiknet
eller MQTT-passordet seg, oppdaterer du på staden — klienten blir bygd opp att
ved behov og koplar til att med dei nye verdiane:

```python
sdk.update_mqtt_credentials(
    username=info["userName"],
    password=info["pwdInfo"],
    auth_headers={"Authorization": f"Bearer {new_token}"},
)
```

Dette er trygt å kalle før ei hendingsløkke finst; attkoplinga blir utsett til
`connect()`.

## Fullstendig døme

`examples/watch_state.py` set det saman: oppdag einingar, hent MQTT-info,
abonner, skriv ut oppdateringar til Ctrl-C, kople frå ryddig. Bruk `--location`
for posisjonar, `--raw` for rå meldingar og `--discover` for å abonnere på alle
emne per funnen eining (og samstundes skrive rå meldingar).

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
        sdk.on_state(lambda m: print("tilstand", m.device_id, m.state, m.battery))
        sdk.on_event(lambda m: print("hending", m.device_id, m.event, m.message))

        stop = asyncio.Event()
        asyncio.get_running_loop().add_signal_handler(signal.SIGINT, stop.set)
        sdk.connect()
        try:
            await stop.wait()
        finally:
            sdk.disconnect()


asyncio.run(main())
```

## Einingsobjekt (`Navimow`, `NavimowCloudDevice`, `StateManager`)

For applikasjonar som vil ha eitt objekt per klippar med eigne abonnentar:

```python
from mower_sdk import Navimow

account = Navimow(client)
cloud = await account.initiate_cloud_connection(devices)   # byggjer NavimowMQTT + NavimowCloud
mowers = account.add_devices(devices)                      # list[NavimowCloudDevice]

async def on_state(state): print(state.state)
mowers[0].state_manager.state_callback.add_subscribers(on_state)
```

`StateManager` held `last_state`, `last_attributes`, `last_event` og tilbyr
`DataEvent`-krokar (`state_callback`, `event_callback`, `attributes_callback`).
Abonnentar blir haldne med svake referansar, så hald ein sterk referanse til
handsamaren din (funksjon på modulnivå eller bunden metode på eit levande
objekt).

> Merk: `NavimowCloud` tolkar for tida emne på forma `navimow/{id}/{kanal}`,
> medan meglaren publiserer `/downlink/vehicle/{id}/realtimeDate/{kanal}`, så
> dette laget tek enno ikkje imot sanntidsmeldingar ende til ende. Bruk
> `NavimowSDK` for sanntidsdata i produksjon.
