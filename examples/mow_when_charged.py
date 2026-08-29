"""Start klipping så snart klipparen melder >= 80 % batteri i ladestasjonen.

Kombinerer eit REST-augneblinksbilete med MQTT-tilstandsoppdateringar.
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
            print("Ingen einingar.")
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
            print(f"Ventar på at {mower.name} når {TARGET_BATTERY} % i ladestasjonen…")
            await ready.wait()
            try:
                await client.async_start_mowing(mower.id)
                print("Klipping starta.")
            except MowerAPIError as err:
                print("Kunne ikkje starte:", err)
        finally:
            sdk.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
