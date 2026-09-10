import pytest

from app.core.balancer import (
    ALL_ACCOUNTS_ADMINISTRATIVELY_UNAVAILABLE,
    ALL_ACCOUNTS_DEACTIVATED,
    ALL_ACCOUNTS_PAUSED,
    ALL_ACCOUNTS_QUOTA_EXCEEDED,
    ALL_ACCOUNTS_RATE_LIMITED,
    ALL_ACCOUNTS_REAUTH_REQUIRED,
    NO_ACCOUNTS_CONFIGURED,
    POOL_UNAVAILABLE_ERROR_CODES,
)
from app.core.resilience.overload import LOCAL_OVERLOAD_CODES
from app.modules.proxy.api import _UNAVAILABLE_SELECTION_ERROR_CODES
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.selection_errors import selection_failure_response
from app.modules.proxy.service import _LOCAL_PROXY_ERROR_CODES


def test_pool_usage_exhaustion_is_codex_compatible_429():
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Usage limit reached",
            error_code="usage_limit_reached",
        )
    )

    assert status == 429
    assert payload == {
        "error": {
            "message": "Usage limit reached",
            "type": "usage_limit_reached",
            "code": "usage_limit_reached",
        }
    }


def test_unusable_pool_remains_no_accounts_503():
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="All accounts require re-authentication",
            error_code=None,
        )
    )

    assert status == 503
    assert payload["error"]["type"] == "server_error"
    assert payload["error"]["code"] == "no_accounts"


def test_pool_usage_exhaustion_preserves_authoritative_reset():
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Rate limit exceeded. Try again in 300s",
            error_code="usage_limit_reached",
            resets_at=1_700_003_600,
        )
    )

    assert status == 429
    assert payload["error"]["resets_at"] == 1_700_003_600


@pytest.mark.parametrize("local_code", sorted(LOCAL_OVERLOAD_CODES))
def test_local_overload_codes_keep_rate_limit_contract(local_code: str):
    # Covers every canonical local capacity code, including codes added later
    # (e.g. api_key_stream_fair_share): local overload must stay a 429
    # rate_limit_error and never be reclassified as upstream usage exhaustion
    # or a 503.
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Local capacity is exhausted",
            error_code=local_code,
        )
    )

    assert status == 429
    assert payload["error"]["type"] == "rate_limit_error"
    assert payload["error"]["code"] == local_code
    assert "resets_at" not in payload["error"]


@pytest.mark.parametrize(
    ("error_code", "message"),
    [
        (NO_ACCOUNTS_CONFIGURED, "No accounts are configured"),
        (ALL_ACCOUNTS_PAUSED, "3 of 3 accounts are paused"),
        (ALL_ACCOUNTS_DEACTIVATED, "3 of 3 accounts are deactivated"),
        (ALL_ACCOUNTS_REAUTH_REQUIRED, "3 of 3 accounts require re-authentication"),
        (
            ALL_ACCOUNTS_ADMINISTRATIVELY_UNAVAILABLE,
            "3 of 3 accounts are administratively unavailable (2 paused, 1 deactivated)",
        ),
        (ALL_ACCOUNTS_QUOTA_EXCEEDED, "No available accounts"),
    ],
)
def test_administratively_dead_pool_keeps_503_and_carries_the_typed_code(error_code: str, message: str):
    status, payload = selection_failure_response(
        AccountSelection(account=None, error_message=message, error_code=error_code)
    )

    assert status == 503
    assert payload["error"]["code"] == error_code
    assert payload["error"]["message"] == message


@pytest.mark.parametrize("error_code", [ALL_ACCOUNTS_RATE_LIMITED, ALL_ACCOUNTS_QUOTA_EXCEEDED])
def test_usage_blocked_pool_with_a_retry_hint_stays_a_429(error_code: str):
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Rate limit exceeded. Try again in 300s",
            error_code=error_code,
        )
    )

    assert status == 429
    assert payload["error"]["type"] == "rate_limit_error"
    assert payload["error"]["code"] == error_code


def test_pool_unavailable_codes_reach_both_local_registries():
    """An unlisted code falls through to 502 and loses local failure attribution."""
    assert POOL_UNAVAILABLE_ERROR_CODES <= _UNAVAILABLE_SELECTION_ERROR_CODES
    assert POOL_UNAVAILABLE_ERROR_CODES <= _LOCAL_PROXY_ERROR_CODES
