# Feil og feilsøking

## Unntakstypar

| Unntak | Kasta av | Eigenskapar |
|---|---|---|
| `MowerAPIError` | Alle REST-kall | `message`, `status_code` (HTTP, kan vere `None`), `error_code` (plattformkode, kan vere `None`) |
| `MowerAuthError` | Reservert for autentiseringsflytar | `message` |
| `MowerMQTTError` | `MowerMQTT` tilkopling/abonnement | `message` |
| `HTTPClientError` | `UrllibSession`-transporten | blir pakka inn i `MowerAPIError` av API-laget |
| `RuntimeError` | Feil bruk av løkke, `NavimowSDK`-kommandoar utan tilkopling | — |

Fang `MowerAPIError` for alt som gjeld REST:

```python
from mower_sdk import MowerAPIError

try:
    await client.async_start_mowing(device_id)
except MowerAPIError as err:
    if err.status_code == 401:
        token = await refresh_token()          # din eigen OAuth2-kode
        client.update_token(token)
    elif err.error_code == "DEVICE_NOT_FOUND":
        ...
    else:
        log.warning("kommando feila: %s", err)   # "melding | HTTP 400 | Error Code: X"
```

`ERROR_MESSAGES` og `COMMAND_ERRORS` er oppslagstabellar med meldingar frå
plattforma (på kinesisk, slik dei blir serverte oppstraums); `error_code`-strengen
er det stabile å matche på.

## Vanlege problem

**`MowerAPIError: … TOKEN_EXPIRED` med `status_code=401` før nokon førespurnad**
— teiknstrengen er tom. Set han eller kall `update_token()`.

**`RuntimeError: NavimowSDK.connect() requires a running event loop or an explicit loop= argument`**
— du kalla `connect()` frå vanleg synkron kode. Anten køyr inne i
`asyncio.run(...)`, eller send `loop=` ved konstruksjon.

**`RuntimeError: This SDK object is being used from a different event loop`**
— eit objekt bunde til éi løkke vart rørt frå ei anna (vanleg når
`asyncio.run()` blir kalla to gonger). Lag SDK-objekta inne i løkka som skal
bruke dei, eller send `loop=` eksplisitt.

**`asyncio.run() cannot be called from a running event loop`**
— du brukte ein blokkerande metode (`discover_devices()`, `start_mowing()`, …)
inne i asynkron kode. Bruk `async_*`-varianten.

**Ingen MQTT-meldingar kjem**
— sjekk, i denne rekkjefølgja: `sdk.is_connected`; at `records=devices` vart
sendt (så abonnementa treffer verkelege ID-ar); at teiknet framleis er gyldig
(WebSocket-handtrykket ber det); og slå på feilsøkingslogging (nedanfor) for å
sjå emne og lastar.

**`MowerAPIError: … Request too frequent. Please retry after 1 minute`**
— `/openapi/mqtt/userInfo/get/v2` er ratebegrensa. Kall
`async_refresh_mqtt_info()` éin gong per prosess og del oppsettet mellom
einingane (slik `examples/watch_mower.py` gjer). Merk at
`MowerClient.async_subscribe_device_updates()` friskar opp oppsettet ved kvart
kall; bruk `NavimowSDK` med `records=devices`, eller `client.mqtt`
direkte, når du følgjer fleire einingar.

**Åtvaring om jokerteikn-abonnement** (`subscribing cloud topics with wildcard`)
— `records` var tom. Send einingslista.

## Logging

SDK-en loggar under namnerommet `mower_sdk`. Tilkoplingsdetaljar blir logga på
INFO med maskerte løyndomar; råe lastar på DEBUG.

```python
import logging
logging.basicConfig(level=logging.INFO)
logging.getLogger("mower_sdk.mqtt").setLevel(logging.DEBUG)
```
