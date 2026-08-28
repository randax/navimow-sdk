"""Modul for hovudklienten.

Gjev eit samla klientgrensesnitt som samlar alle funksjonane.
"""

import asyncio
from typing import Any, Callable, Optional

from mower_sdk.api import MowerAPI
from mower_sdk.http import HTTPSession
from mower_sdk.models import Device, DeviceStatus, MowerCommand
from mower_sdk.mqtt import MowerMQTT


class MowerClient:
    """Hovudklient for robotklipparplattforma.

    Samlar REST-API- og MQTT-funksjonane i eitt felles grensesnitt.

    Eigenskapar:
        api: REST-API-klient
        mqtt: MQTT-klient
    """

    def __init__(
        self,
        session: HTTPSession,
        token: str,
        api_base_url: str = "",
        mqtt_broker: str = "",
        mqtt_port: int = 1883,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        """Initialiser hovudklienten.

        Parametrar:
            session: Asynkron HTTP-økt
            token: Tilgangsteikn
            api_base_url: Grunn-URL for REST-API-et
            mqtt_broker: Adresse til MQTT-meglaren
            mqtt_port: MQTT-porten
            mqtt_username: MQTT-brukarnamn (valfritt)
            mqtt_password: MQTT-passord (valfritt)
        """
        self.api = MowerAPI(session=session, token=token, base_url=api_base_url)
        self.mqtt = MowerMQTT(
            broker=mqtt_broker,
            port=mqtt_port,
            username=mqtt_username,
            password=mqtt_password,
            loop=loop,
        )
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.mqtt_ws_path: Optional[str] = None
        self._token = token

    def update_token(self, token: str) -> None:
        """Oppdater tilgangsteiknet."""
        self._token = token
        self.api.set_token(token)

    async def async_refresh_mqtt_info(self) -> dict[str, Any]:
        """Oppdater MQTT-tilkoplingsinformasjonen asynkront."""
        info = await self.api.async_get_mqtt_user_info()
        mqtt_host = info.get("mqttHost", "")
        mqtt_url = info.get("mqttUrl", "")
        username = info.get("userName")
        password = info.get("pwdInfo")
        self.mqtt_broker = mqtt_host
        self.mqtt_port = 443
        self.mqtt_username = username
        self.mqtt_password = password
        self.mqtt_ws_path = mqtt_url
        auth_headers = {"Authorization": f"Bearer {self._token}"}
        self.mqtt.configure_wss(
            mqtt_host=mqtt_host,
            mqtt_url=mqtt_url,
            username=username,
            password=password,
            auth_headers=auth_headers,
            port=self.mqtt_port,
        )
        return info

    def refresh_mqtt_info(self) -> dict[str, Any]:
        """Oppdater MQTT-tilkoplingsinformasjonen synkront."""
        return asyncio.run(self.async_refresh_mqtt_info())

    async def async_discover_devices(self) -> list[Device]:
        """Oppdag einingar asynkront.

        Retur:
            Einingslista

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return await self.api.async_get_devices()

    def discover_devices(self) -> list[Device]:
        """Oppdag einingar synkront.

        Retur:
            Einingslista

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return self.api.get_devices()

    async def async_subscribe_device_updates(
        self,
        device_id: str,
        callback: Optional[Callable[[DeviceStatus], None]] = None,
    ) -> None:
        """Abonner asynkront på statusoppdateringar for ei eining.

        Parametrar:
            device_id: Eining-ID
            callback: Tilbakekall for statusoppdatering

        Unntak:
            MowerMQTTError: Dersom abonnementet feilar
        """
        await self.async_refresh_mqtt_info()
        await self.mqtt.async_connect()
        await self.mqtt.async_subscribe_device(
            device_id=device_id,
            on_status_update=callback,
        )

    def subscribe_device_updates(
        self,
        device_id: str,
        callback: Optional[Callable[[DeviceStatus], None]] = None,
    ) -> None:
        """Abonner synkront på statusoppdateringar for ei eining.

        Parametrar:
            device_id: Eining-ID
            callback: Tilbakekall for statusoppdatering

        Unntak:
            MowerMQTTError: Dersom abonnementet feilar
        """
        self.refresh_mqtt_info()
        self.mqtt.connect()
        self.mqtt.subscribe_device(
            device_id=device_id,
            on_status_update=callback,
        )

    async def async_start_mowing(self, device_id: str) -> dict[str, Any]:
        """Start klipping asynkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return await self.api.async_send_command(device_id, MowerCommand.START)

    def start_mowing(self, device_id: str) -> dict[str, Any]:
        """Start klipping synkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return self.api.send_command(device_id, MowerCommand.START)

    async def async_pause_mowing(self, device_id: str) -> dict[str, Any]:
        """Set klipping på pause asynkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return await self.api.async_send_command(device_id, MowerCommand.PAUSE)

    def pause_mowing(self, device_id: str) -> dict[str, Any]:
        """Set klipping på pause synkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return self.api.send_command(device_id, MowerCommand.PAUSE)

    async def async_dock(self, device_id: str) -> dict[str, Any]:
        """Returner til ladestasjonen asynkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return await self.api.async_send_command(device_id, MowerCommand.DOCK)

    def dock(self, device_id: str) -> dict[str, Any]:
        """Returner til ladestasjonen synkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return self.api.send_command(device_id, MowerCommand.DOCK)

    async def async_resume(self, device_id: str) -> dict[str, Any]:
        """Hald fram med klippinga asynkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return await self.api.async_send_command(device_id, MowerCommand.RESUME)

    def resume(self, device_id: str) -> dict[str, Any]:
        """Hald fram med klippinga synkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom kommandoen feilar
        """
        return self.api.send_command(device_id, MowerCommand.RESUME)

    def get_cached_status(self, device_id: str) -> Optional[DeviceStatus]:
        """Hent den bufra einingsstatusen.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Einingsstatusen, eller `None` dersom han ikkje finst
        """
        return self.mqtt.get_cached_status(device_id)

    async def async_get_device_status(self, device_id: str) -> DeviceStatus:
        """Hent einingsstatus asynkront via API-et.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Einingsstatus

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return await self.api.async_get_device_status(device_id)

    async def async_get_device_statuses(self, device_ids: list[str]) -> dict[str, DeviceStatus]:
        """Hent einingsstatusar i batch asynkront via API-et.

        Parametrar:
            device_ids: Liste over einings-ID-ar

        Retur:
            Kartlegging frå einings-ID til status

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return await self.api.async_get_device_statuses(device_ids)

    def get_device_status(self, device_id: str) -> DeviceStatus:
        """Hent einingsstatus synkront via API-et.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Einingsstatus

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return self.api.get_device_status(device_id)

    def get_device_statuses(self, device_ids: list[str]) -> dict[str, DeviceStatus]:
        """Hent einingsstatusar i batch synkront via API-et.

        Parametrar:
            device_ids: Liste over einings-ID-ar

        Retur:
            Kartlegging frå einings-ID til status

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return asyncio.run(self.api.async_get_device_statuses(device_ids))

    def get_token(self) -> str:
        """Hent gjeldande tilgangsteikn."""
        return self._token
