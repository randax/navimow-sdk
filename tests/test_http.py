import json
import unittest
from email.message import Message
from importlib import import_module
from unittest import mock
from urllib.request import Request

from tests.support import import_source_module


class FakeHTTPResponse:
    def __init__(self, status, body, charset="utf-8"):
        self.status = status
        self._body = body
        self.headers = Message()
        self.headers["Content-Type"] = f"application/json; charset={charset}"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size=None):
        return self._body if size is None else self._body[:size]


class UrllibSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.http_module = import_source_module("mower_sdk.http")
        self.api_module = import_module("mower_sdk.api")

    async def test_request_serializes_query_json_and_headers(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeHTTPResponse(200, b'{"code": 1}')

        session = self.http_module.UrllibSession(timeout=12.5)
        with mock.patch.object(self.http_module, "_open", side_effect=fake_urlopen):
            async with session.request(
                "POST",
                "https://api.example.test/resource",
                json={"enabled": True},
                params={"device": ["one", "two"]},
                headers={"Authorization": "Bearer example-token"},
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), {"code": 1})

        request = captured["request"]
        self.assertEqual(captured["timeout"], 12.5)
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("device=one&device=two", request.full_url)
        self.assertEqual(json.loads(request.data), {"enabled": True})
        self.assertEqual(request.get_header("Authorization"), "Bearer example-token")
        self.assertEqual(request.get_header("Content-type"), "application/json")

    async def test_transport_errors_are_normalized(self):
        session = self.http_module.UrllibSession()
        with mock.patch.object(self.http_module, "_open", side_effect=OSError("offline")):
            with self.assertRaisesRegex(self.http_module.HTTPClientError, "offline"):
                async with session.request("GET", "https://api.example.test"):
                    pass

    async def test_closed_session_rejects_requests(self):
        session = self.http_module.UrllibSession()
        await session.close()

        with self.assertRaisesRegex(self.http_module.HTTPClientError, "closed"):
            async with session.request("GET", "https://api.example.test"):
                pass

    async def test_oversized_response_is_rejected(self):
        session = self.http_module.UrllibSession(max_response_bytes=4)
        with mock.patch.object(
            self.http_module,
            "_open",
            return_value=FakeHTTPResponse(200, b"12345"),
        ):
            with self.assertRaisesRegex(self.http_module.HTTPClientError, "exceeds 4 bytes"):
                async with session.request("GET", "https://api.example.test"):
                    pass

    async def test_async_context_manager_closes_session(self):
        session = self.http_module.UrllibSession()

        async with session:
            self.assertFalse(session.closed)

        self.assertTrue(session.closed)

    def test_cross_origin_redirect_strips_credentials(self):
        request = Request(
            "https://api.example.test/resource",
            headers={
                "Authorization": "Bearer example-token",
                "Cookie": "session=example-cookie",
                "X-Api-Key": "example-api-key",
                "X-Request-Id": "request-id",
            },
        )

        redirected = self.http_module._SafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            Message(),
            "https://other.example.test/resource",
        )

        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))
        self.assertIsNone(redirected.get_header("Cookie"))
        self.assertIsNone(redirected.get_header("X-api-key"))
        self.assertIsNone(redirected.get_header("X-request-id"))

    async def test_unrelated_session_errors_are_not_wrapped_as_api_failures(self):
        class FailingContext:
            async def __aenter__(self):
                raise RuntimeError("custom session bug")

            async def __aexit__(self, exc_type, exc, traceback):
                return None

        class FailingSession:
            closed = False

            def request(self, *args, **kwargs):
                return FailingContext()

        api = self.api_module.MowerAPI(
            FailingSession(),
            token="token",
            base_url="https://api.example.test",
        )

        with self.assertRaisesRegex(RuntimeError, "custom session bug"):
            await api._async_request("GET", "/resource")

    async def test_transport_errors_are_wrapped_as_api_failures(self):
        api = self.api_module.MowerAPI(
            self.http_module.UrllibSession(),
            token="token",
            base_url="https://api.example.test",
        )
        with mock.patch.object(self.http_module, "_open", side_effect=OSError("offline")):
            with self.assertRaisesRegex(self.api_module.MowerAPIError, "offline"):
                await api._async_request("GET", "/resource")


if __name__ == "__main__":
    unittest.main()
