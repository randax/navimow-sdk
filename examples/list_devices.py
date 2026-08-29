"""List klipparane på kontoen saman med eit statusbilete."""

import asyncio

from _common import make_client, make_session


async def main() -> None:
    async with make_session() as session:
        client = make_client(session)
        devices = await client.async_discover_devices()
        if not devices:
            print("Ingen einingar på denne kontoen.")
            return

        statuses = await client.async_get_device_statuses([d.id for d in devices])
        for device in devices:
            status = statuses.get(device.id)
            state = status.status.value if status else "?"
            battery = f"{status.battery}%" if status else "?"
            print(
                f"{device.name:<20} {device.id:<24} {device.model:<12} "
                f"online={device.online!s:<5} tilstand={state:<10} batteri={battery}"
            )


if __name__ == "__main__":
    asyncio.run(main())
