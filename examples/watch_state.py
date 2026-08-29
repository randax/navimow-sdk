"""Stream live state/event/attribute updates over MQTT until Ctrl-C."""

import asyncio
import logging
import signal

from _common import make_client, make_session
from mower_sdk import (
    DeviceAttributesMessage,
    DeviceEventMessage,
    DeviceStateMessage,
    NavimowSDK,
)


def on_state(msg: DeviceStateMessage) -> None:
    print(f"[state] {msg.device_id}: {msg.state} battery={msg.battery}% pos={msg.position}")


def on_event(msg: DeviceEventMessage) -> None:
    print(f"[event] {msg.device_id}: {msg.type}/{msg.event} {msg.level or ''} {msg.message or ''}")


def on_attributes(msg: DeviceAttributesMessage) -> None:
    print(f"[attrs] {msg.device_id}: {msg.attributes}")


async def main() -> None:
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
        )
        sdk.on_state(on_state)
        sdk.on_event(on_event)
        sdk.on_attributes(on_attributes)

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)

        sdk.connect()
        print(f"Watching {len(devices)} device(s); Ctrl-C to stop.")
        try:
            await stop.wait()
        finally:
            sdk.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
