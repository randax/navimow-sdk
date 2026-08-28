"""Hjelparar for asynkrone tilbakekall."""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Callable
from types import MethodType
from typing import Any, Optional


class Event:
    """Asynkron hending med svake referansar til abonnentar."""

    def __init__(self) -> None:
        self._handlers: list[weakref.ReferenceType[Any]] = []

    def __iadd__(self, handler: Callable[..., Any]) -> "Event":
        if isinstance(handler, MethodType):
            ref: weakref.ReferenceType[Any] = weakref.WeakMethod(handler)
        else:
            ref = weakref.ref(handler)
        self._handlers.append(ref)
        return self

    def __isub__(self, handler: Callable[..., Any]) -> "Event":
        self._handlers = [ref for ref in self._handlers if ref() is not handler]
        return self

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        tasks = []
        for ref in self._handlers:
            func = ref()
            if func is not None:
                tasks.append(func(*args, **kwargs))
        if tasks:
            await asyncio.gather(*tasks)

        self._handlers = [ref for ref in self._handlers if ref() is not None]


class DataEvent:
    """Datahending med asynkron utsending."""

    def __init__(self) -> None:
        self.on_data_event = Event()

    async def data_event(self, data: Optional[Any] = None) -> None:
        """Køyr tilbakekalla for datahendinga."""
        if data is None:
            await self.on_data_event()
        else:
            await self.on_data_event(data)

    def add_subscribers(self, obj_method: Callable[..., Any]) -> None:
        """Legg til abonnentar."""
        self.on_data_event += obj_method

    def remove_subscribers(self, obj_method: Callable[..., Any]) -> None:
        """Fjern abonnentar."""
        try:
            self.on_data_event -= obj_method
        except ValueError:
            pass
