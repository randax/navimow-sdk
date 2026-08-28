"""Modul for eigne unntaksklassar.

Gjev alle tilpassa unntak som SDK-en bruker.
"""

from typing import Optional


class MowerAPIError(Exception):
    """Unntak knytt til API-førespurnader.

    Eigenskapar:
        status_code: HTTP-statuskode dersom ho er tilgjengeleg
        message: Feilmelding
        error_code: Forretningskode dersom ho er tilgjengeleg
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ):
        """Initialiser API-unntaket.

        Parametrar:
            message: Feilmelding
            status_code: HTTP-statuskode
            error_code: Forretningskode
        """
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code

    def __str__(self) -> str:
        """Returner ei formatert feilmelding."""
        parts = [self.message]
        if self.status_code:
            parts.append(f"HTTP {self.status_code}")
        if self.error_code:
            parts.append(f"Error Code: {self.error_code}")
        return " | ".join(parts)


class MowerAuthError(Exception):
    """Unntak knytt til autentisering.

    Eigenskapar:
        message: Feilmelding
    """

    def __init__(self, message: str):
        """Initialiser autentiseringsunntaket.

        Parametrar:
            message: Feilmelding
        """
        super().__init__(message)
        self.message = message


class MowerMQTTError(Exception):
    """Unntak knytt til MQTT.

    Eigenskapar:
        message: Feilmelding
    """

    def __init__(self, message: str):
        """Initialiser MQTT-unntaket.

        Parametrar:
            message: Feilmelding
        """
        super().__init__(message)
        self.message = message


# Oppslagstabell for feilmeldingar
ERROR_MESSAGES = {
    "AUTH_FAILED": "认证失败，请检查 client_id 和 client_secret",
    "TOKEN_EXPIRED": "Token 已过期，请重新登录",
    "TOKEN_REFRESH_FAILED": "Token 刷新失败",
    "DEVICE_NOT_FOUND": "设备未找到",
    "DEVICE_OFFLINE": "设备离线",
    "COMMAND_FAILED": "指令执行失败",
    "API_REQUEST_FAILED": "API 请求失败",
    "MQTT_CONNECTION_FAILED": "MQTT 连接失败",
    "MQTT_SUBSCRIBE_FAILED": "MQTT 订阅失败",
    "INVALID_COMMAND": "无效的指令",
    "INVALID_DEVICE_STATUS": "无效的设备状态",
}

# Feilkartlegging for kommandoar
COMMAND_ERRORS = {
    "START": {
        "DEVICE_OFFLINE": "设备离线，无法启动",
        "ALREADY_MOWING": "设备正在割草中",
        "BATTERY_LOW": "电池电量过低，无法启动",
    },
    "PAUSE": {
        "NOT_MOWING": "设备未在割草中，无法暂停",
        "DEVICE_OFFLINE": "设备离线，无法暂停",
    },
    "DOCK": {
        "ALREADY_DOCKED": "设备已在充电站",
        "DEVICE_OFFLINE": "设备离线，无法返回充电站",
    },
    "RESUME": {
        "NOT_PAUSED": "设备未暂停，无法恢复",
        "DEVICE_OFFLINE": "设备离线，无法恢复",
    },
}
