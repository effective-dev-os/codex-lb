# Architectural Decisions

This file records long-lived architecture decisions for codex-lb. New decisions
are appended and superseded by later entries rather than edited in place.

## ADR-0001: ProxyService target-architecture cutover refactor

- **Date:** 2026-06-05
- **Status:** Accepted
- **Scope:** `app/modules/proxy/service.py` and proxy runtime internals

### Context

`app/modules/proxy/service.py` grew into a large god object that mixed HTTP
bridge orchestration, WebSocket proxying, streaming retry and settlement,
request logging, API-key usage, file operations, compact responses, warmup,
rate-limit payloads, and observability helpers in one `ProxyService` class.

The service is production-critical and contains compatibility behavior that is
hard to safely rewrite in one pass. A big-bang replacement would create high
review risk, hidden behavior drift, and poor rollback properties. Random small
file-shaving also fails to create durable boundaries and lets the façade grow
again during later feature work.

### Decision

Use a **target-architecture cutover refactor** for proxy service decomposition.

`app/modules/proxy/service.py` remains the stable public façade and compatibility
import surface. Extracted implementation lives behind the private package
`app/modules/proxy/_service/`.

Small cohesive domains are leaf modules under `_service/`. Large domains are
folder packages with their own internal structure:

```text
app/modules/proxy/
├── service.py                    # public façade / compatibility surface
├── _support.py                   # compatibility shim
├── _warmup.py                    # compatibility shim
└── _service/                     # private implementation package
    ├── support.py
    ├── warmup.py
    ├── api_key_usage.py
    ├── request_log.py
    ├── rate_limit.py
    ├── file_ops.py
    ├── transcribe.py
    ├── codex_control.py
    ├── compact.py
    ├── observability.py
    ├── http_bridge/              # future: session lifecycle, relay, retry
    ├── websocket/                # future: connect, request state, replay
    ├── streaming/                # future: retry, once, settlement
    └── account_selection/        # future: budget, failover, admission
```

For high-risk future domains such as HTTP bridge, WebSocket, streaming, and
account selection, use this sequence:

1. Add characterization/golden-master tests for the current behavior
2. Use Mikado-style discovery to map hidden couplings and prerequisites
3. Introduce narrow protocols/adapters where needed, following Branch by
   Abstraction inside the monolith
4. Build the target domain package behind the façade
5. Switch `ProxyService` to the new package in one controlled cutover
6. Keep the codebase releasable at each commit

Architecture fitness functions are part of the decision. They ratchet the
current decomposition direction and prevent accidental regression:

- `service.py` must stay below the accepted line-count threshold
- `ProxyService` maximum method span must not grow beyond the accepted threshold
- `_support.py` and `_warmup.py` must remain compatibility shims only
- `service.py` must preserve required façade re-exports for existing consumers
- `_service/*` modules must not form arbitrary cross-domain dependencies

### Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Big-bang rewrite of `ProxyService` | Too much behavior would move at once; difficult to review, test, and roll back |
| Continue small ad-hoc extraction PRs | Reduces line count but does not create durable domain ownership or prevent regression |
| Split into external microservices now | Deployment/runtime complexity is unnecessary for the current problem; this is an internal modularity issue first |
| Keep `service.py` as the implementation owner indefinitely | Preserves compatibility but leaves the god-object failure mode in place |

### Consequences

- Future proxy work should add new domain behavior under `_service/`, not directly
  into `service.py`
- Compatibility shims may remain while internal consumers migrate, but they must
  not gain new behavior
- Large domain migrations should prefer complete package cutovers with targeted
  characterization tests over shallow helper movement
- CI can enforce architectural ratchets through `scripts/check_proxy_architecture.py`
- The accepted thresholds are starting ratchets, not final goals; future PRs
  should lower them after major domain packages are extracted

## ADR-0002: Degradation is declared from account-status evidence, never from selector prose

- **Date:** 2026-09-10
- **Status:** Accepted
- **Scope:** `app/core/balancer/`, `app/modules/proxy/load_balancer.py`,
  `app/core/resilience/circuit_breaker.py`

### Context

An administratively dead pool reported `"No available accounts"` with no
`error_code`, and `LoadBalancer` string-matched that prose to declare the whole
fleet degraded. The most evidence-starved path in the selector was the only one
wired to global degradation.

The evidence was never lost. `_selectable_accounts()` drops every account whose
status is `PAUSED`, `DEACTIVATED` or `REAUTH_REQUIRED` before the states the
selector classifies are built, but `selection_inputs.runtime_accounts` still
carries those accounts. Nothing consulted them, so every status-classification
arm in `select_account` filtered an already-empty list and produced nothing.

Measured cost, 2026-09-03..09-10: four accounts were deactivated by a 404 from
the ChatGPT usage endpoint, the pool served zero completions for seven days,
and the only thing anyone could read was `"No available accounts. Service is
operating in degraded mode: all upstream accounts are unavailable"` — a
sentence that names neither the condition nor the remedy.

A second, independent defect compounded it: `are_all_account_circuit_breakers_open()`
quantified `all()` over a lazily populated dict, so one OPEN breaker for the
single account that had ever failed satisfied the quantifier and declared a
healthy fleet degraded.

### Decision

Degradation is declared from a typed selection code backed by an account-status
census, not from the text of an error message.

- `AccountStatusCensus` carries per-status counts for the configured fleet.
  It is **advisory only**: it may not enter a candidate pool, a sort key, or a
  strategy branch. Selection order for a pool with at least one selectable
  account is unchanged.
- The selector classifies an empty candidate pool from the census and returns a
  typed code plus a message naming the real condition
  (`"3 of 3 accounts are paused"`).
- The census speaks only when it covers the whole configured fleet. A pool
  narrowed by request scope — owner-restricted continuity, for one — still falls
  through as before, so one blocked account is never reported as a fleet-wide
  condition.
- `LoadBalancer` escalates on membership in the typed-code set. The prose match
  is retained solely as the fallback for the still-uncoded empty-pool case, so
  no escalation that fires today stops firing.
- A circuit breaker that does not exist means an account that has never failed.
  `are_all_account_circuit_breakers_open()` takes the configured account ids and
  treats a missing breaker as closed.

Every new code is registered in both literal registries outside
`selection_errors.py` — `_UNAVAILABLE_SELECTION_ERROR_CODES` (unlisted codes
fall through to 502) and `_LOCAL_PROXY_ERROR_CODES` (failure-log attribution
and the stream-error rewrite). A code added to one and not the other is a
silent behavior change, not a partial rollout.

### Alternatives considered

| Alternative | Why rejected |
| --- | --- |
| Keep the prose match, improve the wording | The defect is that a string is the contract; a better string is the same defect |
| Let paused/deactivated accounts through the filter | Fail-closed there is deliberate; the bug is illegibility, not exclusion |
| Return 429 instead of 503 for a dead pool | The status code is not the lever — a client router treats 408/409/429 and everything >=500 as retry-eligible alike |
| Report the census from the dashboard only | The engineer reading a 503 body is the one who needs it |

### Consequences

- A whole-fleet `RATE_LIMITED` or `QUOTA_EXCEEDED` census now carries a code
  where all three returns previously carried none.
- Callers that branched on the message text must branch on `error_code`.
  `_account_selection_recovery_sleep_seconds` was updated accordingly; its
  invariant — an administratively dead pool gets no recovery sleep — is
  unchanged.
- The typed code is legible to a human reading `error.message` through a
  LiteLLM proxy, not to a client branching on `error.code`: LiteLLM discards the
  upstream envelope and stamps `code = str(status_code)`.
- This makes the failure legible. It does not make a dead pool serve traffic.
