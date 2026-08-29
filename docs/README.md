# Dokumentasjon for Navimow-SDK-en

`randax-navimow-sdk` (importert som `mower_sdk`) snakkar med Navimow-skya på
vegner av ein Navimow-konto: han listar klipparar, les status, sender
kommandoar over REST og strøymer sanntidstilstand over MQTT.

| Rettleiing | Kva ho dekkjer |
|---|---|
| [Kom i gang](getting-started.md) | Installer, skaff teikn, list klipparar, send ein kommando |
| [Slik verkar SDK-en](architecture.md) | Lag, transportar, emne og reglar for hendingsløkka |
| [REST: oppdaging, status, kommandoar](rest.md) | `MowerClient` / `MowerAPI` i djupna |
| [Sanntidsoppdateringar over MQTT](realtime.md) | `NavimowSDK`, tilbakekall, mellomlager, attkopling |
| [Datamodellar](models.md) | `Device`, `DeviceStatus`, MQTT-meldingsklassar, opprekningar |
| [Feil og feilsøking](errors.md) | Unntakstypar, vanlege feil, logging |
| [Home Assistant og andre hendingsløkker](event-loops.md) | Løkkeeigarskap, trådtryggleik, HA-mønster |

Køyrbare skript ligg i [`../examples`](../examples). Dei les
`NAVIMOW_TOKEN` og `NAVIMOW_API_URL` frå miljøet, så du kan prøve dei utan å
endre kode:

```bash
export NAVIMOW_TOKEN="eyJ..."
export NAVIMOW_API_URL="https://<navimow-openapi-vert>"
python examples/list_devices.py
```
