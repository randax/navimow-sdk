"""HTTP-transportar for SDK-en."""

import asyncio
import json as json_module
from typing import Any, Optional, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class HTTPClientError(Exception):
    """Normalisert feil frå HTTP-transporten."""


class HTTPResponse(Protocol):
    """Minste svargrensesnitt som REST-klienten brukar."""

    status: int

    async def text(self) -> str:
        """Returner svarteksten."""

    async def json(self) -> Any:
        """Tolk svaret som JSON."""


class HTTPRequestContext(Protocol):
    """Asynkron kontekst for ei HTTP-førespurnad."""

    async def __aenter__(self) -> HTTPResponse:
        """Utfør førespurnaden og returner svaret."""

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """Frigjer svarressursane."""


class HTTPSession(Protocol):
    """Strukturelt øktgrensesnitt som òg aiohttp oppfyller."""

    @property
    def closed(self) -> bool:
        """Vis om økta er stengd."""

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> HTTPRequestContext:
        """Lag ein asynkron førespurnadskontekst."""


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Fjern legitimasjonar frå omdirigeringar til andre opphav."""

    _SAFE_CROSS_ORIGIN_HEADERS = {
        "accept",
        "accept-encoding",
        "accept-language",
        "content-type",
        "user-agent",
    }

    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Optional[Request]:
        redirected = super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )
        if redirected is None or _origin(request.full_url) == _origin(new_url):
            return redirected
        for header, _value in redirected.header_items():
            if header.lower() not in self._SAFE_CROSS_ORIGIN_HEADERS:
                redirected.remove_header(header)
        return redirected


def _origin(url: str) -> tuple[str, Optional[str], Optional[int]]:
    parsed = urlsplit(url)
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), parsed.hostname, parsed.port or default_port


def _open(request: Request, timeout: float) -> Any:
    return build_opener(_SafeRedirectHandler()).open(request, timeout=timeout)


class _UrllibResponse:
    def __init__(self, status: int, body: bytes, charset: str) -> None:
        self.status = status
        self._body = body
        self._charset = charset

    async def text(self) -> str:
        return self._body.decode(self._charset, errors="replace")

    async def json(self) -> Any:
        return json_module.loads(await self.text())


class _UrllibRequestContext:
    def __init__(
        self,
        session: "UrllibSession",
        method: str,
        url: str,
        data: Optional[dict[str, Any]],
        params: Optional[dict[str, Any]],
        headers: Optional[dict[str, str]],
    ) -> None:
        self._session = session
        self._method = method
        self._url = url
        self._data = data
        self._params = params
        self._headers = headers

    async def __aenter__(self) -> HTTPResponse:
        if self._session.closed:
            raise HTTPClientError("HTTP session is closed")
        try:
            return await asyncio.to_thread(self._execute)
        except HTTPClientError:
            raise
        except (OSError, URLError) as exc:
            raise HTTPClientError(str(exc)) from exc

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    def _execute(self) -> _UrllibResponse:
        url = self._url
        if self._params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(self._params, doseq=True)}"

        headers = dict(self._headers or {})
        body = None
        if self._data is not None:
            body = json_module.dumps(self._data).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")

        request = Request(url, data=body, headers=headers, method=self._method)
        try:
            with _open(request, self._session.timeout) as response:
                return _response_from_file(
                    response.status,
                    _read_body(response, self._session.max_response_bytes),
                    response.headers,
                )
        except HTTPError as exc:
            try:
                return _response_from_file(
                    exc.code,
                    _read_body(exc, self._session.max_response_bytes),
                    exc.headers,
                )
            finally:
                exc.close()
        except (OSError, URLError) as exc:
            raise HTTPClientError(str(exc)) from exc


def _response_from_file(status: int, body: bytes, headers: Any) -> _UrllibResponse:
    charset = "utf-8"
    if headers is not None:
        charset = headers.get_content_charset() or charset
    return _UrllibResponse(status, body, charset)


def _read_body(response: Any, limit: int) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise HTTPClientError(f"HTTP response exceeds {limit} bytes")
    return cast(bytes, body)


class UrllibSession:
    """Asynkron HTTP-økt bygd på standardbiblioteket."""

    def __init__(self, timeout: float = 30.0, max_response_bytes: int = 16 * 1024 * 1024) -> None:
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.closed = False

    def request(
        self,
        method: str,
        url: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> HTTPRequestContext:
        return _UrllibRequestContext(self, method, url, json, params, headers)

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> "UrllibSession":
        if self.closed:
            raise HTTPClientError("HTTP session is closed")
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()
