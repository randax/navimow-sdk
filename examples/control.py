"""Tiny CLI: list | status <id> | start|pause|resume|dock <id>."""

import asyncio
import sys

from _common import make_client, make_session
from mower_sdk import MowerAPIError, MowerClient

ACTIONS = {
    "start": MowerClient.async_start_mowing,
    "pause": MowerClient.async_pause_mowing,
    "resume": MowerClient.async_resume,
    "dock": MowerClient.async_dock,
}


async def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("list", "status", *ACTIONS):
        print(__doc__)
        return 2

    async with make_session() as session:
        client = make_client(session)
        try:
            if argv[0] == "list":
                for d in await client.async_discover_devices():
                    print(d.id, d.name)
                return 0

            device_id = argv[1]
            if argv[0] == "status":
                s = await client.async_get_device_status(device_id)
                print(f"{s.status.value} battery={s.battery}% error={s.error_code.value}")
                if s.position:
                    print(f"position={s.position}")
                return 0

            await ACTIONS[argv[0]](client, device_id)
            print(f"{argv[0]} sent to {device_id}")
            return 0
        except MowerAPIError as err:
            print(f"error: {err}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
