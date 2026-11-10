from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, TypedDict

from app.db.models import AccountStatus


class UpstreamError(TypedDict, total=False):
    message: str
    resets_at: int | float
    resets_in_seconds: int | float


FailureClass = Literal["rate_limit", "quota", "retryable_transient", "non_retryable"]
FailurePhase = Literal["connect", "first_event", "mid_stream"]


class ClassifiedFailure(TypedDict):
    failure_class: FailureClass
    phase: FailurePhase
    error_code: str
    error: UpstreamError
    http_status: int | None


@dataclass(frozen=True, slots=True)
class AccountStatusCensus:
    """Status counts for every configured account, as selection-failure evidence.

    Advisory only: the selector reads it to explain an empty candidate pool and
    must never let it enter a candidate pool, a sort key, or a strategy branch.
    """

    total: int
    counts: Mapping[AccountStatus, int]

    @classmethod
    def from_statuses(cls, statuses: Iterable[AccountStatus | str]) -> "AccountStatusCensus":
        counts: Counter[AccountStatus] = Counter()
        total = 0
        for status in statuses:
            total += 1
            try:
                counts[AccountStatus(status)] += 1
            except ValueError:
                continue
        return cls(total=total, counts=dict(counts))

    def count(self, *statuses: AccountStatus) -> int:
        return sum(self.counts.get(status, 0) for status in statuses)
