"""Shared helpers for the example scripts."""

import os
import sys

from mower_sdk import MowerClient, UrllibSession


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Set {name} in the environment (see docs/README.md)")
    return value


def make_session() -> UrllibSession:
    return UrllibSession()


def make_client(session: UrllibSession) -> MowerClient:
    return MowerClient(
        session=session,
        token=env("NAVIMOW_TOKEN"),
        api_base_url=env("NAVIMOW_API_URL"),
    )
