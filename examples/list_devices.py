"""List the mowers on the account together with a status snapshot."""

import asyncio

from _common import make_client, make_session


async def main() -> None:
    async with make_session() as session:
        client = make_client(session)
        devices = await client.async_discover_devices()
        if not devices:
            print("No devices on this account.")
            return

        statuses = await client.async_get_device_statuses([d.id for d in devices])
        for device in devices:
            status = statuses.get(device.id)
            state = status.status.value if status else "?"
            battery = f"{status.battery}%" if status else "?"
            print(
                f"{device.name:<20} {device.id:<24} {device.model:<12} "
                f"online={device.online!s:<5} state={state:<10} battery={battery}"
            )


if __name__ == "__main__":
    asyncio.run(main())
