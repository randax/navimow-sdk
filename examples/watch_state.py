"""Strøym tilstand/hendingar/attributtar over MQTT til Ctrl-C."""

import argparse
import asyncio
import logging
import signal

from _common import make_client, make_session

from mower_sdk import (
    DeviceAttributesMessage,
    DeviceEventMessage,
    DeviceLocationMessage,
    DeviceStateMessage,
    NavimowSDK,
)


def on_state(msg: DeviceStateMessage) -> None:
    print(f"[tilstand] {msg.device_id}: {msg.state} batteri={msg.battery}% pos={msg.position}")


def on_event(msg: DeviceEventMessage) -> None:
    print(
        f"[hending] {msg.device_id}: {msg.type}/{msg.event} {msg.level or ''} {msg.message or ''}"
    )


def on_attributes(msg: DeviceAttributesMessage) -> None:
    print(f"[attr] {msg.device_id}: {msg.attributes}")


def on_location(msg: DeviceLocationMessage) -> None:
    print(
        f"[posisjon] {msg.device_id}: x={msg.x} y={msg.y} theta={msg.theta} "
        f"progress={msg.mowing_percentage}% zone={msg.current_zone} "
        f"zone_progress={msg.zone_progress}% status={msg.status}"
    )


def on_raw(topic: str, payload: bytes) -> None:
    print(f"[rå] {topic}: {payload.decode('utf-8', errors='replace')}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Følg Navimow-sanndata over MQTT.")
    parser.add_argument("--location", action="store_true", help="Ta med posisjonskanalen.")
    parser.add_argument("--raw", action="store_true", help="Skriv alle rå MQTT-meldingar.")
    parser.add_argument(
        "--discover", action="store_true", help="Abonner på alle emne for dei funne einingane."
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    async with make_session() as session:
        client = make_client(session)
        devices = await client.async_discover_devices()
        await client.async_refresh_mqtt_info()

        sdk = NavimowSDK(
            broker=client.mqtt_broker,
            port=client.mqtt_port,
            username=client.mqtt_username,
            password=client.mqtt_password,
            ws_path=client.mqtt_ws_path,
            auth_headers={"Authorization": f"Bearer {client.get_token()}"},
            records=devices,
            subscribe_location=args.location,
            extra_topics=(
                [f"/downlink/vehicle/{device.id}/#" for device in devices]
                if args.discover
                else None
            ),
        )
        sdk.on_state(on_state)
        sdk.on_event(on_event)
        sdk.on_attributes(on_attributes)
        if args.location:
            sdk.on_location(on_location)
        if args.raw or args.discover:
            sdk.on_raw(on_raw)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        sdk.connect()
        print(f"Følgjer {len(devices)} eining(ar); Ctrl-C for å stoppe.")
        try:
            await stop.wait()
        finally:
            sdk.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
