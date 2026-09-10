from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import aiohttp
import pytest

from app.core.resilience import circuit_breaker as cb_module
from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    are_all_account_circuit_breakers_open,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_circuit_opens_after_failure_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60)

    async def fail() -> None:
        raise asyncio.TimeoutError

    for _ in range(5):
        with pytest.raises(asyncio.TimeoutError):
            await breaker.call(fail())

    assert breaker.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_transitions_to_half_open_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=10)
    breaker._state = CircuitState.OPEN
    breaker._last_failure_time = 0.0

    monkeypatch.setattr(cb_module.time, "monotonic", lambda: 11.0)

    assert breaker.state == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_half_open_closes_after_success_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=10, success_threshold=2)
    breaker._state = CircuitState.OPEN
    breaker._failure_count = 5
    breaker._last_failure_time = 0.0

    monkeypatch.setattr(cb_module.time, "monotonic", lambda: 11.0)

    async def succeed() -> str:
        return "ok"

    assert await breaker.call(succeed()) == "ok"
    assert breaker.state == CircuitState.HALF_OPEN
    assert await breaker.call(succeed()) == "ok"
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_4xx_errors_do_not_count_as_failures() -> None:
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60)

    request_info = MagicMock()
    request_info.real_url = "https://example.test"

    async def fail_4xx() -> None:
        raise aiohttp.ClientResponseError(
            request_info=request_info,
            history=(),
            status=404,
            message="not found",
            headers=None,
        )

    for _ in range(5):
        with pytest.raises(aiohttp.ClientResponseError):
            await breaker.call(fail_4xx())

    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_open_state_fast_fails_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    breaker = CircuitBreaker(failure_threshold=5, recovery_timeout_seconds=60)
    breaker._state = CircuitState.OPEN
    breaker._last_failure_time = 0.0
    monkeypatch.setattr(cb_module.time, "monotonic", lambda: 1.0)

    class ShouldNotBeAwaited:
        def __await__(self):
            raise AssertionError("request coroutine must not run when circuit is open")

    with pytest.raises(CircuitBreakerOpenError):
        await breaker.call(ShouldNotBeAwaited())


@pytest.mark.asyncio
async def test_all_account_breakers_open_ignores_accounts_without_a_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One tripped account must not declare a three-account fleet degraded."""
    tripped = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=60)
    tripped._state = CircuitState.OPEN
    monkeypatch.setattr(cb_module, "_account_circuit_breakers", {"acc-1": tripped})

    assert are_all_account_circuit_breakers_open(["acc-1", "acc-2", "acc-3"]) is False
    assert are_all_account_circuit_breakers_open(["acc-1"]) is True
    assert are_all_account_circuit_breakers_open([]) is False
