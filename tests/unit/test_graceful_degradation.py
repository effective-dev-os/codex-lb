from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Iterator, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.balancer import (
    ALL_ACCOUNTS_ADMINISTRATIVELY_UNAVAILABLE,
    ALL_ACCOUNTS_DEACTIVATED,
    ALL_ACCOUNTS_PAUSED,
    ALL_ACCOUNTS_QUOTA_EXCEEDED,
    ALL_ACCOUNTS_RATE_LIMITED,
    ALL_ACCOUNTS_REAUTH_REQUIRED,
    NO_ACCOUNTS_CONFIGURED,
)
from app.core.openai.model_registry import ModelRegistry, ReasoningLevel, UpstreamModel
from app.core.resilience.degradation import get_status, is_degraded, set_degraded, set_normal
from app.db.models import Account, AccountStatus
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy import load_balancer as load_balancer_module
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickyOwnerLookup, StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_degradation_state() -> Iterator[None]:
    set_normal()
    yield
    set_normal()


def _model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="balanced"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset(),
        raw={},
    )


class _StubAccountsRepository:
    def __init__(self, accounts: list[object]) -> None:
        self._accounts = accounts

    async def list_accounts(self) -> list[object]:
        return list(self._accounts)

    async def update_status_if_current(self, *args, **kwargs) -> bool:
        return True

    async def update_status(self, *args, **kwargs) -> bool:
        return True


class _StubUsageRepository:
    async def latest_by_account(self, window: str | None = None) -> dict[str, object]:
        return {}


class _StubStickyRepository:
    async def get_account_id(self, *args, **kwargs) -> str | None:
        return None

    async def get_account_id_and_abandonment(self, *args, **kwargs) -> StickyOwnerLookup:
        return StickyOwnerLookup(account_id=None, continuity_abandoned=False)

    async def upsert(self, *args, **kwargs):
        return None

    async def delete(self, *args, **kwargs) -> bool:
        return False


class _StubAdditionalUsageRepository:
    async def latest_by_account(self, *args, **kwargs) -> dict[str, object]:
        return {}

    async def latest_by_quota_key(self, *args, **kwargs) -> dict[str, object]:
        return {}


@asynccontextmanager
async def _repo_factory(accounts: list[object]):
    yield ProxyRepositories(
        accounts=cast(AccountsRepository, _StubAccountsRepository(accounts)),
        usage=cast(UsageRepository, _StubUsageRepository()),
        request_logs=cast(RequestLogsRepository, SimpleNamespace()),
        sticky_sessions=cast(StickySessionsRepository, _StubStickyRepository()),
        api_keys=cast(ApiKeysRepository, SimpleNamespace()),
        additional_usage=cast(AdditionalUsageRepository, _StubAdditionalUsageRepository()),
    )


def test_set_degraded_sets_status() -> None:
    set_degraded("all upstream accounts are unavailable")

    assert is_degraded() is True
    assert get_status() == {
        "level": "degraded",
        "reason": "all upstream accounts are unavailable",
    }


def test_set_normal_clears_degraded_state() -> None:
    set_degraded("temporary outage")

    set_normal()

    assert is_degraded() is False
    assert get_status() == {"level": "normal", "reason": None}


@pytest.mark.asyncio
async def test_health_ready_succeeds_when_degraded() -> None:
    from app.modules.health.api import health_ready

    set_degraded("all upstream accounts are unavailable")
    mock_session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch("app.core.draining._draining", False), patch("app.modules.health.api.get_session") as mock_get_session:

        async def mock_get_session_context():
            yield mock_session

        mock_get_session.return_value = mock_get_session_context()

        result = await health_ready()

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_model_registry_keeps_cached_models_when_refresh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"plus": [_model("cached-model")]})
    cached_models = set(registry.get_models_with_fallback())

    def _raise_runtime_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.core.openai.model_registry.ModelRegistrySnapshot", _raise_runtime_error)

    with pytest.raises(RuntimeError, match="boom"):
        await registry.update({"pro": [_model("new-model")]})

    assert "cached-model" in cached_models
    assert set(registry.get_models_with_fallback()) == cached_models


@pytest.mark.asyncio
async def test_load_balancer_returns_degraded_message_when_no_accounts_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False)
    )

    balancer = LoadBalancer(lambda: _repo_factory([]))
    selection = await balancer.select_account()

    assert selection.account is None
    assert selection.error_code == NO_ACCOUNTS_CONFIGURED
    assert selection.error_message == (
        "No accounts are configured. Service is operating in degraded mode: no accounts are configured"
    )
    assert is_degraded() is True


@pytest.mark.asyncio
async def test_load_balancer_clears_stale_degraded_state_for_typed_selection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False)
    )

    balancer = LoadBalancer(lambda: _repo_factory([]))
    monkeypatch.setattr(
        balancer,
        "_load_selection_inputs",
        AsyncMock(
            return_value=load_balancer_module._SelectionInputs(
                accounts=[],
                latest_primary={},
                latest_secondary={},
                latest_monthly={},
                error_message="No accounts with a plan supporting model 'gpt-5.3-codex-spark'",
                error_code=load_balancer_module.NO_PLAN_SUPPORT_FOR_MODEL,
            )
        ),
    )

    set_degraded("all upstream accounts are unavailable")
    selection = await balancer.select_account(model="gpt-5.3-codex-spark")

    assert selection.account is None
    assert selection.error_code == load_balancer_module.NO_PLAN_SUPPORT_FOR_MODEL
    assert is_degraded() is False


def _configured_account(account_id: str, status: AccountStatus) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime.now(timezone.utc),
        status=status,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "error_code", "error_message", "degraded_reason"),
    [
        (
            [AccountStatus.PAUSED] * 3,
            ALL_ACCOUNTS_PAUSED,
            "3 of 3 accounts are paused",
            "3 of 3 accounts are paused",
        ),
        (
            [AccountStatus.DEACTIVATED] * 3,
            ALL_ACCOUNTS_DEACTIVATED,
            "3 of 3 accounts are deactivated",
            "3 of 3 accounts are deactivated",
        ),
        (
            [AccountStatus.REAUTH_REQUIRED] * 3,
            ALL_ACCOUNTS_REAUTH_REQUIRED,
            "3 of 3 accounts require re-authentication",
            "3 of 3 accounts are awaiting re-authentication",
        ),
        (
            [AccountStatus.PAUSED, AccountStatus.PAUSED, AccountStatus.DEACTIVATED],
            ALL_ACCOUNTS_ADMINISTRATIVELY_UNAVAILABLE,
            "3 of 3 accounts are administratively unavailable (2 paused, 1 deactivated)",
            "none of 3 accounts are available (2 paused, 1 deactivated)",
        ),
    ],
)
async def test_administratively_dead_pool_names_the_account_condition(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[AccountStatus],
    error_code: str,
    error_message: str,
    degraded_reason: str,
) -> None:
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False)
    )
    accounts = [_configured_account(f"census-{index}", status) for index, status in enumerate(statuses)]

    balancer = LoadBalancer(lambda: _repo_factory(list(accounts)))
    selection = await balancer.select_account()

    assert selection.account is None
    assert selection.error_code == error_code
    assert selection.error_message == (f"{error_message}. Service is operating in degraded mode: {degraded_reason}")
    assert is_degraded() is True
    assert get_status()["reason"] == degraded_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reset_at", "error_code", "degraded_reason"),
    [
        (AccountStatus.RATE_LIMITED, 3600, ALL_ACCOUNTS_RATE_LIMITED, "3 of 3 accounts are rate limited"),
        (AccountStatus.QUOTA_EXCEEDED, None, ALL_ACCOUNTS_QUOTA_EXCEEDED, "3 of 3 accounts are over quota"),
    ],
)
async def test_usage_blocked_pool_names_the_account_condition(
    monkeypatch: pytest.MonkeyPatch,
    status: AccountStatus,
    reset_at: int | None,
    error_code: str,
    degraded_reason: str,
) -> None:
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False)
    )
    accounts = [_configured_account(f"usage-{index}", status) for index in range(3)]
    if reset_at is not None:
        for account in accounts:
            account.reset_at = int(time.time()) + reset_at

    balancer = LoadBalancer(lambda: _repo_factory(list(accounts)))
    selection = await balancer.select_account()

    assert selection.account is None
    assert selection.error_code == error_code
    assert selection.error_message is not None
    assert selection.error_message.endswith(f"Service is operating in degraded mode: {degraded_reason}")
    assert is_degraded() is True


@pytest.mark.asyncio
async def test_one_active_account_still_routes_with_paused_siblings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.proxy.load_balancer.get_settings", lambda: SimpleNamespace(circuit_breaker_enabled=False)
    )
    accounts = [
        _configured_account("mixed-active", AccountStatus.ACTIVE),
        _configured_account("mixed-paused-1", AccountStatus.PAUSED),
        _configured_account("mixed-paused-2", AccountStatus.PAUSED),
    ]
    set_degraded("all upstream accounts are unavailable")

    balancer = LoadBalancer(lambda: _repo_factory(list(accounts)))
    selection = await balancer.select_account()

    assert selection.account is not None
    assert selection.account.id == "mixed-active"
    assert selection.error_code is None
    assert is_degraded() is False
