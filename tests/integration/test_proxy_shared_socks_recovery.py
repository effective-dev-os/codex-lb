from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp_socks import ProxyConnector, ProxyTimeoutError
from sqlalchemy import select

from app.core.clients import proxy as core_proxy
from app.core.resilience import network_recovery
from app.db.models import ApiKeyLimit, ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy import service as proxy_service
from tests.integration.test_proxy_transient_retry import _import_account, _success_sse_event
from tests.unit.test_shared_socks_recovery import _FailingSession

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "websocket"])
async def test_keyed_responses_shared_socks_timeout_recovers_owner_and_settles_once(
    async_client, monkeypatch, transport
):
    upstream_account_id = "acc_shared_socks_owner"
    owner = await _import_account(async_client, upstream_account_id, "shared-socks@example.com")
    key_response = await async_client.post(
        "/api/api-keys", json={"name": "shared-socks-fixture", "weeklyTokenLimit": 1000000}
    )
    assert key_response.status_code == 200
    key = key_response.json()
    assert (await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})).status_code == 200
    service = get_proxy_service_for_app(async_client._transport.app)
    monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", AsyncMock(return_value=owner))
    error_health = AsyncMock()
    route_backoff = AsyncMock()
    monkeypatch.setattr(service._load_balancer, "record_error", error_health)
    monkeypatch.setattr(service._load_balancer, "record_error_backoff", route_backoff)
    rotate = AsyncMock(return_value="rotated")
    monkeypatch.setattr(network_recovery, "rotate_shared_http_transport", rotate)
    monkeypatch.setattr(network_recovery, "backoff_seconds", lambda attempt: 0.0)
    monkeypatch.setattr(core_proxy, "discover_native_egress_client", lambda: None)
    monkeypatch.setattr(core_proxy, "_account_circuit_breaker", lambda *args: None)
    connector = ProxyConnector.from_url("socks5://127.0.0.1:9")
    session = _FailingSession(connector, ProxyTimeoutError("Proxy connection timed out: 8.0"))
    accounts = []
    settlement_calls = []
    real_settle = ApiKeysRepository.settle_usage_reservation

    async def settle(repo, reservation_id, **kwargs):
        settlement_calls.append((reservation_id, kwargs))
        return await real_settle(repo, reservation_id, **kwargs)

    async def stream(payload, headers, access_token, account_id, **kwargs):
        accounts.append(account_id)
        assert payload.previous_response_id == "resp_owned_shared_socks"
        if len(accounts) == 1:
            kwargs["session"] = cast(aiohttp.ClientSession, session)
            kwargs["upstream_stream_transport_override"] = transport
            async for event in core_proxy.stream_responses(payload, headers, access_token, account_id, **kwargs):
                yield event
            return
        yield _success_sse_event("resp_shared_socks_recovered")

    monkeypatch.setattr(ApiKeysRepository, "settle_usage_reservation", settle)
    monkeypatch.setattr(proxy_service, "core_stream_responses", stream)
    try:
        response = await async_client.post(
            "/v1/responses",
            headers={"authorization": f"Bearer {key['key']}"},
            json={
                "model": "gpt-5.1",
                "instructions": "synthetic",
                "input": [],
                "stream": True,
                "previous_response_id": "resp_owned_shared_socks",
                "prompt_cache_key": "shared-socks-thread",
            },
        )
        assert response.status_code == 200
        assert "resp_shared_socks_recovered" in response.text
        assert "response.failed" not in response.text
        assert accounts == [upstream_account_id, upstream_account_id]
        assert await service.drain_persistence_tasks(timeout_seconds=2)
        assert len(settlement_calls) == 1
        async with SessionLocal() as db:
            rows = (
                (await db.execute(select(ApiKeyUsageReservation).where(ApiKeyUsageReservation.api_key_id == key["id"])))
                .scalars()
                .all()
            )
            limits = (await db.execute(select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key["id"]))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status != "reserved"
        assert rows[0].input_tokens == rows[0].output_tokens == 1
        assert len(limits) == 1
        assert limits[0].current_value == 2
        error_health.assert_not_awaited()
        route_backoff.assert_not_awaited()
        rotate.assert_awaited_once_with(
            transport="stream", request_id=rotate.await_args.kwargs["request_id"], failed_session=session
        )
    finally:
        await connector.close()
