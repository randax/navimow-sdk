"""Følg ein Navimow-klippar via SDK-en: REST-status først, så sanntid over MQTT.

Bygd om frå eit skript som snakka rått med paho. SDK-en tek seg av
WSS-oppsettet (vert, sti, TLS, klient-ID), abonnementa, tolking av meldingane
og trådtrygg utsending av tilbakekalla på asyncio-løkka.

Bruk:
    python examples/watch_mower.py [--token-file navimow_tokens.json] [--no-location]

Teiknet blir lese frå NAVIMOW_TOKEN, elles frå tokenfila ({"access_token": "..."}).
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from mower_sdk import (
    DeviceAttributesMessage,
    DeviceEventMessage,
    DeviceLocationMessage,
    DeviceStateMessage,
    MowerAPIError,
    MowerClient,
    NavimowSDK,
    UrllibSession,
)

API_BASE_URL = os.environ.get("NAVIMOW_API_URL", "https://navimow-fra.ninebot.com")


def load_token(token_file: Path) -> str:
    token = os.environ.get("NAVIMOW_TOKEN")
    if token:
        return token
    try:
        return str(json.loads(token_file.read_text(encoding="utf-8"))["access_token"])
    except (OSError, ValueError, KeyError) as err:
        sys.exit(f"Fann ikkje tilgangsteikn: set NAVIMOW_TOKEN eller lag {token_file} ({err})")


def on_state(msg: DeviceStateMessage) -> None:
    print(f"[tilstand] {msg.device_id}: {msg.state} batteri={msg.battery}% ts={msg.timestamp}")


def on_event(msg: DeviceEventMessage) -> None:
    print(
        f"[hending]  {msg.device_id}: {msg.type}/{msg.event} {msg.level or ''} {msg.message or ''}"
    )


def on_attributes(msg: DeviceAttributesMessage) -> None:
    print(f"[attr]     {msg.device_id}: {msg.attributes}")


def on_location(msg: DeviceLocationMessage) -> None:
    print(
        f"[posisjon] {msg.device_id}: x={msg.x} y={msg.y} theta={msg.theta} "
        f"type={msg.type} framdrift={msg.mowing_percentage}% "
        f"sone={msg.current_zone} soneframdrift={msg.zone_progress}% status={msg.status}"
    )


def on_raw(topic: str, payload: bytes) -> None:
    print(f"[rå] {topic}: {payload.decode('utf-8', errors='replace')}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--token-file", type=Path, default=Path("navimow_tokens.json"))
    parser.add_argument("--no-location", action="store_true", help="Ikkje abonner på posisjon.")
    parser.add_argument("--raw", action="store_true", help="Skriv òg alle rå meldingar.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    token = load_token(args.token_file)

    # UrllibSession treng ingen ekstra avhengnader; ein aiohttp.ClientSession fungerer like godt.
    async with UrllibSession() as session:
        client = MowerClient(session=session, token=token, api_base_url=API_BASE_URL)

        try:
            devices = await client.async_discover_devices()
        except MowerAPIError as err:
            sys.exit(f"Oppdaging feila: {err}")
        if not devices:
            sys.exit("Ingen einingar på kontoen.")
        for device in devices:
            print(
                f"Eining: {device.name} ({device.id}) modell={device.model} online={device.online}"
            )

        # REST-augneblinksbilete før sanntidsstraumen (REST heng 1–2 minutt etter MQTT)
        statuses = await client.async_get_device_statuses([d.id for d in devices])
        for device_id, status in statuses.items():
            print(f"Status:  {device_id}: {status.status.value} batteri={status.battery}%")

        # Hent MQTT-oppsettet ÉIN gong. Endepunktet er ratebegrensa
        # («Request too frequent. Please retry after 1 minute»).
        await client.async_refresh_mqtt_info()

        sdk = NavimowSDK(
            broker=client.mqtt_broker,  # t.d. wss://mqtt-fra.navimow.com – SDK-en tolkar URL-en
            port=client.mqtt_port,  # 443
            username=client.mqtt_username,
            password=client.mqtt_password,
            ws_path=client.mqtt_ws_path,  # slår på WebSocket + TLS
            auth_headers={"Authorization": f"Bearer {client.get_token()}"},
            records=devices,  # abonnerer per einings-ID, ikkje med jokerteikn
            subscribe_location=not args.no_location,
        )
        sdk.on_state(on_state)
        sdk.on_event(on_event)
        sdk.on_attributes(on_attributes)
        if not args.no_location:
            sdk.on_location(on_location)
        if args.raw:
            sdk.on_raw(on_raw)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        sdk.connect()  # bind til den køyrande løkka; attkopling er automatisk
        print(f"\nFølgjer {len(devices)} eining(ar). Ctrl-C for å stoppe.\n")
        try:
            await stop.wait()
        finally:
            sdk.disconnect()
            print("Fråkopla.")


if __name__ == "__main__":
    asyncio.run(main())
