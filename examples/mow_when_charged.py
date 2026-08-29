"""Start mowing as soon as the mower reports >= 80% battery while docked.

Combines a REST snapshot with MQTT state updates.
"""

import asyncio

from _common import make_client, make_session
from mower_sdk import DeviceStateMessage, MowerAPIError, NavimowSDK

TARGET_BATTERY = 80


async def main() -> None:
    async with make_session() as session:
        client = make_client(session)
        devices = await client.async_discover_devices()
        if not devices:
            print("No devices.")
            return
        mower = devices[0]
        await client.async_refresh_mqtt_info()

        loop = asyncio.get_running_loop()
        ready = asyncio.Event()

        def on_state(msg: DeviceStateMessage) -> None:
            if msg.device_id != mower.id:
                return
            print(f"{msg.state} {msg.battery}%")
            if msg.state in ("docked", "charging") and (msg.battery or 0) >= TARGET_BATTERY:
                loop.call_soon(ready.set)

        sdk = NavimowSDK(
            broker=client.mqtt_broker,
            port=client.mqtt_port,
            username=client.mqtt_username,
            password=client.mqtt_password,
            ws_path=client.mqtt_ws_path,
            auth_headers={"Authorization": f"Bearer {client.get_token()}"},
            records=[mower],
        )
        sdk.on_state(on_state)
        sdk.connect()
        try:
            print(f"Waiting for {mower.name} to reach {TARGET_BATTERY}% while docked…")
            await ready.wait()
            try:
                await client.async_start_mowing(mower.id)
                print("Mowing started.")
            except MowerAPIError as err:
                print("Could not start:", err)
        finally:
            sdk.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
