## ADDED Requirements

### Requirement: Shared SOCKS pre-dispatch failures preserve account-neutral recovery

Direct Python Responses HTTP and WebSocket attempts using the shared SOCKS connector MUST classify typed proxy connection or proxy timeout failures that prove no upstream dispatch as `proxy_network_unavailable` before recording account circuit-breaker health. Such failures MUST retain the concrete failed shared session and pre-dispatch retry provenance, recover only on the same account within the existing monotonic request deadline, and preserve existing reservation settlement and account-owned continuation semantics. A configured environment proxy alone or arbitrary message text MUST NOT establish this classification. Dedicated account proxy routes and ambiguous post-dispatch failures MUST retain their existing scope and replay rules.

#### Scenario: Shared SOCKS timeout recovers on the selected account

- **GIVEN** a direct Responses attempt uses the shared SOCKS connector
- **WHEN** a typed SOCKS CONNECT timeout proves no request bytes reached upstream
- **THEN** the attempt carries `proxy_network_unavailable` with its failed shared session
- **AND** bounded recovery retries the same account without account backoff, circuit-breaker failure, or sticky reassignment
- **AND** the request settles its reservation exactly once

#### Scenario: Dedicated account proxy failure remains endpoint scoped

- **GIVEN** a Responses attempt uses a resolved account proxy route
- **WHEN** that endpoint has a confirmed pre-dispatch connection failure
- **THEN** the existing endpoint-scoped failure and permitted account failover behavior remain unchanged

#### Scenario: Ambiguous shared transport delivery is not replayed

- **WHEN** a shared SOCKS connection fails after upstream dispatch may have begun
- **THEN** the failure gains no new same-account or cross-account replay eligibility
