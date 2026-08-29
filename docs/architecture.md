# Slik verkar SDK-en

## Lag

```
┌──────────────────────────────────────────────────────────────┐
│  Applikasjonen din / Home Assistant-integrasjon              │
├──────────────────────────────────────────────────────────────┤
│  MowerClient          NavimowSDK            Navimow          │
│  (REST-fasade +       (MQTT-fasade med      (konto +         │
│   teiknhaldar)         typa tilbakekall)     einingsobjekt)  │
├───────────────┬──────────────────────┬───────────────────────┤
│  MowerAPI     │  NavimowMQTT         │  NavimowCloud         │
│  REST-kall    │  paho-mqtt over WSS  │  NavimowCloudDevice   │
│               │                      │  StateManager         │
├───────────────┼──────────────────────┴───────────────────────┤
│  HTTPSession  │  paho-mqtt Client (eigen nettverkstråd)      │
│  (Urllib /    │                                              │
│   aiohttp)    │                                              │
└───────────────┴──────────────────────────────────────────────┘
```

Vel inngangen som passar jobben:

| Du vil … | Bruk |
|---|---|
| Liste klipparar, lese eit statusbilete, starte/pause/halde fram/dokke | `MowerClient` (eller `MowerAPI` direkte) |
| Ta imot tilstand/hendingar/attributtar etter kvart som dei skjer | `NavimowSDK` |
| Modellere kvar klippar som eit objekt med eigen sist kjend tilstand og abonnentar | `Navimow` → `NavimowCloudDevice` → `StateManager` |
| Bruke din eigen HTTP-klient | Kva som helst objekt som oppfyller `HTTPSession`-protokollen i `mower_sdk.http` |

## REST-transport

`MowerAPI` byggjer kvar førespurnad på same måte:

1. `Authorization: Bearer <teikn>` og eit ferskt `requestId`-UUID-hovud.
2. `session.request(method, url, json=..., params=..., headers=...)` brukt som
   asynkron kontekstbehandlar.
3. HTTP ≥ 400 → `MowerAPIError(status_code=...)`.
4. Kroppen er JSON med eit `code`-felt; alt anna enn `code == 1` →
   `MowerAPIError` med `desc` frå tenaren.
5. Transportfeil (`HTTPClientError`, `aiohttp.ClientError`) blir pakka inn i
   `MowerAPIError`, så kallarar berre treng fange éin type.

Endepunkt som blir brukte:

| Metode | Sti | Føremål |
|---|---|---|
| GET | `/openapi/smarthome/authList` | List autoriserte einingar |
| POST | `/openapi/smarthome/getVehicleStatus` | Status for fleire einings-ID-ar |
| POST | `/openapi/smarthome/sendCommands` | Send ein kommando i Google-Smart-Home-stil |
| POST | `/openapi/smarthome/responseCommands` | Hent resultat av tidlegare kommandoar |
| GET | `/openapi/mqtt/userInfo/get/v2` | Hent MQTT-vert, WebSocket-sti og legitimasjon |

`MowerCommand`-verdiar blir omsette til kommandovokabularet til plattforma:

| `MowerCommand` | Kommando på leidninga | params |
|---|---|---|
| `START` | `action.devices.commands.StartStop` | `{"on": true}` |
| `STOP` | `action.devices.commands.StartStop` | `{"on": false}` |
| `PAUSE` | `action.devices.commands.PauseUnpause` | `{"on": false}` |
| `RESUME` | `action.devices.commands.PauseUnpause` | `{"on": true}` |
| `DOCK` | `action.devices.commands.Dock` | — |

Kommandoresultatet `alreadyInState` blir rekna som vellykka, så «start» på ein
klippar som alt klipper kastar ikkje unntak.

## MQTT-transport

`NavimowMQTT` pakkar inn `paho-mqtt` (tilbakekall-API v2). Tilkoplingsdetaljane
kjem frå `/openapi/mqtt/userInfo/get/v2`; `MowerClient.async_refresh_mqtt_info()`
hentar dei og lagrar `mqtt_broker`, `mqtt_username`, `mqtt_password` og
`mqtt_ws_path` på klienten.

- Transporten er **WebSocket + TLS** når ein `ws_path` er gjeven (noko skya
  alltid gjev); port 443.
- Klient-ID-en blir avleidd av brukarnamnet, så éin konto kan ha fleire
  tilkoplingar utan kollisjon.
- Keepalive er 2400 s som standard; automatisk attkopling ventar frå 1 s opp
  til 60 s.
- Ved kvar (att)kopling abonnerer klienten på, per einings-ID:

  ```
  /downlink/vehicle/{device_id}/realtimeDate/state
  /downlink/vehicle/{device_id}/realtimeDate/event
  /downlink/vehicle/{device_id}/realtimeDate/attributes
  ```

  Utan einings-ID-ar fell han tilbake til jokerteiknet `+`.

- Innkomande lastar er JSON; einings-ID-en frå emnet blir lagd inn som
  `device_id` før tolking til `DeviceStateMessage`, `DeviceEventMessage` eller
  `DeviceAttributesMessage`.

Paho køyrer nettverksløkka si i **ein eigen tråd**. Kvart tilbakekall som når
koden din blir flytta over til asyncio-løkka SDK-en er bunden til, via
`loop.call_soon_threadsafe(asyncio.create_task, coro)`. Difor er løkkereglane
nedanfor viktige.

## Eigarskap til hendingsløkka

- Send `loop=` for å binde eit SDK-objekt til ei bestemt løkke for heile
  levetida.
- Elles bind objektet seg til den **køyrande** løkka første gongen
  tilkoplingsarbeid skjer (`connect()`, `async_connect()`, `connect_async()`).
- Å bruke objektet frå ei anna løkke seinare kastar
  `RuntimeError("This SDK object is being used from a different event loop")`.
- Ei stengd løkke blir avvist med ein gong.
- Kall alltid `disconnect()` før du stengjer løkka; elles blir MQTT-tilbakekall
  kasta med ein feilsøkingslogg.

Sjå [Home Assistant og andre hendingsløkker](event-loops.md) for mønster.

## Plasshaldarar

To delar av kodebasen er framleis stubbar og er dokumenterte berre for
fullstendig oversikt:

- `MowerMQTT` (brukt av `MowerClient.subscribe_device_updates`) abonnerer på
  `device/{id}/status` — eit TODO-emneskjema. Bruk `NavimowSDK` for
  sanntidsdata.
- `NavimowCloud` tolkar emne på forma `navimow/{id}/{kanal}`, medan meglaren
  publiserer `/downlink/vehicle/...`. Objektmodellen `Navimow` /
  `NavimowCloudDevice` tek difor enno ikkje imot sanntidsmeldingar frå den
  verkelege meglaren; han fungerer med `NavimowSDK`-mellomlageret eller di eiga
  utsending.
- `NavimowSDK.start_mowing()` osb. publiserer til `navimow/{id}/command`. For
  påliteleg styring brukar du REST-kommandoane på `MowerClient`.
