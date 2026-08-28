"""Modul med hjelpefunksjonar.

Gjev ulike hjelpefunksjonar som SDK-en bruker.
"""

import json
import logging
from datetime import datetime
from typing import Any, Union, cast


def setup_logger(name: str = "mower_sdk", level: int = logging.INFO) -> logging.Logger:
    """Set opp og returner ein loggskrivar.

    Parametrar:
        name: Namnet på loggskrivaren
        level: Loggnivået

    Retur:
        Den konfigurerte loggskrivaren
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def parse_json(data: Union[str, bytes]) -> Union[dict[str, Any], list[Any]]:
    """Tolk JSON-streng.

    Parametrar:
        data: JSON-streng eller byte

    Retur:
        Den tolka ordboka eller lista

    Unntak:
        ValueError: Dersom JSON-tolkinga feilar
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return cast(Union[dict[str, Any], list[Any]], json.loads(data))


def timestamp_to_datetime(timestamp: int) -> datetime:
    """Gjer om eit tidsstempel til eit datetime-objekt.

    Parametrar:
        timestamp: Unix-tidsstempel i sekund

    Retur:
        Eit datetime-objekt
    """
    return datetime.fromtimestamp(timestamp)


def datetime_to_timestamp(dt: datetime) -> int:
    """Gjer om eit datetime-objekt til eit tidsstempel.

    Parametrar:
        dt: Eit datetime-objekt

    Retur:
        Unix-tidsstempel i sekund
    """
    return int(dt.timestamp())
