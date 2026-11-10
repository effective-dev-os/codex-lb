from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp_socks import ProxyConnector, ProxyTimeoutError
from python_socks import ProxyTimeoutError as PythonSocksProxyTimeoutError

from app.core.clients import proxy
from app.core.clients.codex import CodexClient, CodexTransportError
from app.core.config.settings import Settings
from app.core.openai.requests import ResponsesRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


class _FailingSession:
    def __init__(self, connector: aiohttp.BaseConnector, error: Exception) -> None:
        self.connector = connector
        self.error = error

    def post(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        raise self.error


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        upstream_base_url="https://example.invalid/backend-api",
        upstream_connect_timeout_seconds=8.0,
        stream_idle_timeout_seconds=45.0,
        trace_channels=frozenset(),
        proxy_request_budget_seconds=5.0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "websocket"])
@pytest.mark.parametrize("error_type", [ProxyTimeoutError, PythonSocksProxyTimeoutError])
async def test_shared_socks_connect_timeout_preserves_recovery_provenance(monkeypatch, transport, error_type):
    connector = ProxyConnector.from_url("socks5://127.0.0.1:9")
    session = _FailingSession(connector, error_type("Proxy connection timed out: 8.0"))
    breaker = SimpleNamespace(
        pre_call_check=AsyncMock(return_value=False),
        _record_failure=AsyncMock(),
        _record_success=AsyncMock(),
        release_half_open_probe=AsyncMock(),
    )
    monkeypatch.setattr(proxy, "get_settings", _settings)
    monkeypatch.setattr(proxy, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(proxy, "_account_circuit_breaker", lambda *args: breaker)
    payload = ResponsesRequest(model="gpt-5.1", instructions="synthetic", input=[])
    try:
        with pytest.raises(proxy.ProxyResponseError) as raised:
            _ = [
                event
                async for event in proxy.stream_responses(
                    payload,
                    headers={},
                    access_token="synthetic",
                    account_id="account-shared-socks",
                    session=cast(aiohttp.ClientSession, session),
                    raise_for_status=True,
                    upstream_stream_transport_override=transport,
                )
            ]
        error = raised.value
        assert error.payload["error"]["code"] == "proxy_network_unavailable"
        assert error.failure_phase == "connect"
        assert error.retryable_same_contract is True
        assert error.failed_session is session
        assert not proxy.is_confirmed_pre_dispatch_transport_error(error)
        breaker._record_failure.assert_not_awaited()
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_dedicated_route_does_not_inherit_shared_socks_failure_scope(monkeypatch):
    connector = ProxyConnector.from_url("socks5://127.0.0.1:9")
    session = _FailingSession(connector, ProxyTimeoutError("unused shared session"))
    route = ResolvedUpstreamRoute(
        "account_bound", "pool-account", ResolvedProxyEndpoint("endpoint-account", "socks5", "proxy.invalid", 1080)
    )
    routed_error = CodexTransportError(
        "Dedicated proxy connection failed",
        error_code="upstream_unavailable",
        failure_phase="connect",
        retryable_same_contract=True,
    )
    client = SimpleNamespace(request=AsyncMock(side_effect=routed_error))
    monkeypatch.setattr(proxy, "get_settings", _settings)
    payload = ResponsesRequest(model="gpt-5.1", instructions="synthetic", input=[])
    try:
        with pytest.raises(proxy.ProxyResponseError) as raised:
            _ = [
                event
                async for event in proxy.stream_responses(
                    payload,
                    headers={},
                    access_token="synthetic",
                    account_id="account-dedicated",
                    session=cast(aiohttp.ClientSession, session),
                    route=route,
                    codex_client=cast(CodexClient, client),
                    raise_for_status=True,
                    upstream_stream_transport_override="http",
                )
            ]
        assert raised.value.payload["error"]["code"] == "upstream_unavailable"
        assert proxy.is_confirmed_pre_dispatch_transport_error(raised.value)
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_shared_socks_ambiguous_payload_failure_is_not_replayed(monkeypatch):
    connector = ProxyConnector.from_url("socks5://127.0.0.1:9")
    session = _FailingSession(connector, aiohttp.ClientPayloadError("response body interrupted"))
    monkeypatch.setattr(proxy, "get_settings", _settings)
    monkeypatch.setattr(proxy, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(proxy, "_account_circuit_breaker", lambda *args: None)
    payload = ResponsesRequest(model="gpt-5.1", instructions="synthetic", input=[])
    try:
        events = [
            event
            async for event in proxy.stream_responses(
                payload,
                headers={},
                access_token="synthetic",
                account_id="account-ambiguous",
                session=cast(aiohttp.ClientSession, session),
                raise_for_status=True,
                upstream_stream_transport_override="http",
            )
        ]
        assert len(events) == 1
        assert '"code":"upstream_unavailable"' in events[0]
        assert "proxy_network_unavailable" not in events[0]
    finally:
        await connector.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("typed_error", [True, False])
async def test_shared_proxy_scope_requires_actual_connector_and_typed_provenance(monkeypatch, typed_error):
    connector = aiohttp.TCPConnector() if typed_error else ProxyConnector.from_url("socks5://127.0.0.1:9")
    error = ProxyTimeoutError("timed out") if typed_error else RuntimeError("Proxy connection timed out: 8.0")
    session = _FailingSession(connector, error)
    monkeypatch.setattr(proxy, "get_settings", _settings)
    monkeypatch.setattr(proxy, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(proxy, "_account_circuit_breaker", lambda *args: None)
    try:
        events = [
            event
            async for event in proxy.stream_responses(
                ResponsesRequest(model="gpt-5.1", instructions="synthetic", input=[]),
                headers={},
                access_token="synthetic",
                account_id="account-no-provenance",
                session=cast(aiohttp.ClientSession, session),
                raise_for_status=True,
                upstream_stream_transport_override="http",
            )
        ]
        assert len(events) == 1
        assert "proxy_network_unavailable" not in events[0]
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_shared_socks_error_after_response_headers_does_not_gain_replay(monkeypatch):
    connector = ProxyConnector.from_url("socks5://127.0.0.1:9")
    session = _FailingSession(connector, RuntimeError("unused"))
    monkeypatch.setattr(proxy, "_account_circuit_breaker", lambda *args: None)

    @asynccontextmanager
    async def response_context():
        yield SimpleNamespace(status=200)

    error = ProxyTimeoutError("typed error after dispatch")
    try:
        with pytest.raises(ProxyTimeoutError) as raised:
            async with proxy._service_circuit_breaker_context(
                response_context(),
                settings=cast(Settings, _settings()),
                shared_proxy_session=cast(aiohttp.ClientSession, session),
            ):
                raise error
        assert raised.value is error
    finally:
        await connector.close()
