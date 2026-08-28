"""Modul for REST-API-klienten.

Gjev funksjonar for å snakke med REST-API-et til robotklipparplattforma.
"""

import asyncio
import uuid
from typing import Any, Optional, cast

import aiohttp

from mower_sdk.errors import ERROR_MESSAGES, MowerAPIError
from mower_sdk.models import Device, DeviceStatus, MowerCommand


class MowerAPI:
    """REST-API-klient.

    Gjev synkrone og asynkrone grensesnitt mot API-et til robotklipparplattforma.

    Eigenskapar:
        base_url: Grunn-URL for API-et (TODO: Konfigurer den verkelege API-grunn-URL-en)
        session: aiohttp-økt (asynkron)
        token: Tilgangsteikn
    """

    def __init__(self, session: aiohttp.ClientSession, token: str, base_url: str):
        """Initialiser API-klienten.

        Parametrar:
            session: aiohttp-økt
            token: Tilgangsteikn
            base_url: Grunn-URL for API-et
        """
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._token = token

    def set_token(self, token: str) -> None:
        """Oppdater tilgangsteiknet."""
        self._token = token

    def _get_auth_headers(self) -> dict[str, str]:
        """Hent autentiseringshovudet."""
        if not self._token:
            raise MowerAPIError(
                ERROR_MESSAGES["TOKEN_EXPIRED"],
                status_code=401,
                error_code="TOKEN_EXPIRED",
            )
        return {"Authorization": f"Bearer {self._token}"}

    async def _async_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send ei asynkron HTTP-førespurnad.

        Parametrar:
            method: HTTP-metode (GET, POST, PUT, DELETE)
            endpoint: API-endepunkt (relativ sti)
            data: Forespurnadsdata (valfri)
            params: Spørjeparametrar (valfrie)

        Retur:
            JSON-data frå svaret

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self._get_auth_headers()
        headers["requestId"] = str(uuid.uuid4())

        try:
            session = self._session
            async with session.request(
                method, url, json=data, params=params, headers=headers
            ) as response:
                if response.status >= 400:
                    error_text = await response.text()
                    raise MowerAPIError(
                        f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {error_text}",
                        status_code=response.status,
                    )

                return cast(dict[str, Any], await response.json())
        except aiohttp.ClientError as e:
            raise MowerAPIError(f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {str(e)}") from e

    async def async_get_devices(self) -> list[Device]:
        """Hent einingslista asynkront.

        Retur:
            Einingslista

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        response = await self._async_request("GET", "/openapi/smarthome/authList")
        if response.get("code") != 1:
            raise MowerAPIError(f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {response.get('desc')}")
        payload = response.get("data", {}).get("payload", {})
        devices_data = payload.get("devices", [])
        return [Device.from_dict(device_data) for device_data in devices_data]

    async def async_get_mqtt_user_info(self) -> dict[str, Any]:
        """Hent MQTT-tilkoplingsinformasjon asynkront.

        Retur:
            Data for MQTT-tilkoplingsinformasjon

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        response = await self._async_request("GET", "/openapi/mqtt/userInfo/get/v2")
        if response.get("code") != 1:
            raise MowerAPIError(f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {response.get('desc')}")
        return cast(dict[str, Any], response.get("data", {}))

    def get_devices(self) -> list[Device]:
        """Hent einingslista synkront.

        Retur:
            Einingslista

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        return asyncio.run(self.async_get_devices())

    def get_mqtt_user_info(self) -> dict[str, Any]:
        """Hent MQTT-tilkoplingsinformasjon synkront."""
        return asyncio.run(self.async_get_mqtt_user_info())

    async def async_get_device_statuses(self, device_ids: list[str]) -> dict[str, DeviceStatus]:
        """Hent einingsstatusar i batch asynkront.

        Parametrar:
            device_ids: Liste over einings-ID-ar

        Retur:
            Kartlegging frå einings-ID til status

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        if not device_ids:
            return {}
        response = await self._async_request(
            "POST",
            "/openapi/smarthome/getVehicleStatus",
            data={"devices": [{"id": device_id} for device_id in device_ids]},
        )
        if response.get("code") != 1:
            raise MowerAPIError(f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {response.get('desc')}")
        payload = response.get("data", {}).get("payload", {})
        devices_data = payload.get("devices", [])
        result: dict[str, DeviceStatus] = {}
        for status_data in devices_data:
            status = DeviceStatus.from_dict(status_data)
            if status.device_id:
                result[status.device_id] = status
        return result

    async def async_get_device_status(self, device_id: str) -> DeviceStatus:
        """Hent einingsstatus asynkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Einingsstatus

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar eller eininga ikkje blir funnen
        """
        try:
            statuses = await self.async_get_device_statuses([device_id])
            status = statuses.get(device_id)
            if not status:
                raise MowerAPIError(
                    ERROR_MESSAGES["DEVICE_NOT_FOUND"],
                    status_code=404,
                    error_code="DEVICE_NOT_FOUND",
                )
            return status
        except MowerAPIError as e:
            if e.status_code == 404:
                raise MowerAPIError(
                    ERROR_MESSAGES["DEVICE_NOT_FOUND"],
                    status_code=404,
                    error_code="DEVICE_NOT_FOUND",
                ) from e
            raise

    def get_device_status(self, device_id: str) -> DeviceStatus:
        """Hent einingsstatus synkront.

        Parametrar:
            device_id: Eining-ID

        Retur:
            Einingsstatus

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar eller eininga ikkje blir funnen
        """
        return asyncio.run(self.async_get_device_status(device_id))

    async def async_send_command(self, device_id: str, command: MowerCommand) -> dict[str, Any]:
        """Send ein kontrollkommando asynkront.

        Parametrar:
            device_id: Eining-ID
            command: Kontrollkommando

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar eller kommandoen ikkje kan fullførast
        """
        command_mapping: dict[MowerCommand, tuple[str, Optional[dict[str, Any]]]] = {
            MowerCommand.START: (
                "action.devices.commands.StartStop",
                {"on": True},
            ),
            MowerCommand.STOP: (
                "action.devices.commands.StartStop",
                {"on": False},
            ),
            MowerCommand.PAUSE: (
                "action.devices.commands.PauseUnpause",
                {"on": False},
            ),
            MowerCommand.RESUME: (
                "action.devices.commands.PauseUnpause",
                {"on": True},
            ),
            MowerCommand.DOCK: ("action.devices.commands.Dock", None),
        }
        if command not in command_mapping:
            raise MowerAPIError(
                ERROR_MESSAGES["INVALID_COMMAND"],
                error_code="INVALID_COMMAND",
            )
        command_name, params = command_mapping[command]
        execution: dict[str, Any] = {"command": command_name}
        if params is not None:
            execution["params"] = params

        response = await self._async_request(
            "POST",
            "/openapi/smarthome/sendCommands",
            data={"commands": [{"devices": [{"id": device_id}], "execution": execution}]},
        )
        if response.get("code") != 1:
            raise MowerAPIError(f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {response.get('desc')}")
        payload = response.get("data", {}).get("payload", {})
        command_results = payload.get("commands", [])
        for result in command_results:
            if result.get("status") == "ERROR":
                error_code = result.get("errorCode") or "COMMAND_FAILED"
                # Rekn som vellykka når eininga alt er i måltilstanden, så vi unngår dobbeltklikk eller feil ved statusdrift.
                if error_code == "alreadyInState":
                    continue
                raise MowerAPIError(
                    f"{ERROR_MESSAGES['COMMAND_FAILED']}: {error_code}",
                    error_code=error_code,
                )
        return cast(dict[str, Any], response.get("data", {}))

    def send_command(self, device_id: str, command: MowerCommand) -> dict[str, Any]:
        """Send ein kontrollkommando synkront.

        Parametrar:
            device_id: Eining-ID
            command: Kontrollkommando

        Retur:
            Resultat av kommandoen

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar eller kommandoen ikkje kan fullførast
        """
        return asyncio.run(self.async_send_command(device_id, command))

    async def async_query_command_results(
        self, devices: list[dict[str, str]]
    ) -> list[dict[str, Any]]:
        """Hent resultata for kommandoar asynkront.

        Parametrar:
            devices: Liste over einingar for kommandoar, med id og cmdNum

        Retur:
            Liste over kommandosvar

        Unntak:
            MowerAPIError: Dersom førespurnaden feilar
        """
        if not devices:
            return []
        response = await self._async_request(
            "POST",
            "/openapi/smarthome/responseCommands",
            data={"devices": devices},
        )
        if response.get("code") != 1:
            raise MowerAPIError(f"{ERROR_MESSAGES['API_REQUEST_FAILED']}: {response.get('desc')}")
        payload = response.get("data", {}).get("payload", {})
        return cast(list[dict[str, Any]], payload.get("devices", []))

    def query_command_results(self, devices: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Hent resultata for kommandoar synkront."""
        return asyncio.run(self.async_query_command_results(devices))

    def __del__(self):
        """Rydd opp ressursane."""
        if hasattr(self, "_session") and self._session and not self._session.closed:
            # Merk: `await` kan ikkje brukast i `__del__`; her prøver vi berre å stengje.
            # Ein betre praksis er å bruke ein kontekststyrar eller kalle `close` eksplisitt.
            pass
