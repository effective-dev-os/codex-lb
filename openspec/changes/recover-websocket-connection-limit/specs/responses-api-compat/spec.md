## ADDED Requirements

### Requirement: Connection lifetime limits recover reactively on the same account

When an upstream Responses WebSocket emits `websocket_connection_limit_reached`, direct WebSocket and HTTP bridge handlers MUST classify it separately from `previous_response_not_found` and `websocket_stream_limit_reached`. The service MUST attempt at most one safe replay on a fresh socket belonging to the same account within the original request deadline. An unanchored request MAY replay its existing safe body. An anchored request MUST retain proven retry-safe full input before removing its anchor. The service MUST NOT replay delta-only anchored requests, requests with meaningful output, sequenced downstream frames, competing pending work, an exhausted replay budget, or an expired deadline. Safe accepted-only lifecycle replay MUST preserve the downstream response identity and suppress duplicate lifecycle preludes. Connection-limit retry and refusal MUST NOT penalize account health and MUST produce distinct metric and log outcomes. Recovery MUST be reactive and MUST NOT introduce a connection-age timer.

#### Scenario: Safe request recovers on its current account

- **GIVEN** a sole pending request before meaningful output with remaining deadline and replay budget
- **AND** it is unanchored or retains proven retry-safe full input
- **WHEN** its upstream socket emits `websocket_connection_limit_reached`
- **THEN** the service replaces that socket on the same account and replays the safe body once
- **AND** only a proven full-input replacement omits `previous_response_id`
- **AND** the request keeps its reservation and admission ownership until normal settlement
- **AND** the connection-limit outcome is logged and counted without penalizing account health

#### Scenario: Unsafe or exhausted request fails without replay

- **GIVEN** a delta-only anchored request, visible or sequenced output, competing pending work, expired deadline, or an already consumed replay
- **WHEN** its upstream socket emits `websocket_connection_limit_reached`
- **THEN** the service does not replay it and settles its terminal exactly once
- **AND** the original upstream connection-limit reason remains distinct from an anchor rejection
- **AND** account health is unchanged

#### Scenario: Accepted-only replay preserves response identity

- **GIVEN** a safe accepted response whose lifecycle prelude was delivered without meaningful or sequenced output
- **WHEN** the connection-limit error triggers its sole replacement
- **THEN** replacement events retain the original downstream response id
- **AND** duplicate created and in-progress preludes are suppressed

#### Scenario: Replacement waits outlive request ownership or deadline

- **GIVEN** a safe connection-limit replay awaiting replacement connection, admission, or an account create lease
- **WHEN** its original deadline expires or its request becomes detached, draining, or abandoned during that wait
- **THEN** the service MUST recheck deadline and active request ownership before dispatching the replacement body
- **AND** it MUST NOT send the body or settle an already settled request again

#### Scenario: Other WebSocket errors retain their existing contracts

- **WHEN** upstream emits `previous_response_not_found` or `websocket_stream_limit_reached`
- **THEN** the existing error classifier and recovery contract apply unchanged

#### Scenario: Lifetime error has no matching request

- **GIVEN** an upstream connection-limit terminal without an active matching request
- **WHEN** either surface handles that terminal
- **THEN** it MUST retire the socket after any pending work drains and record a refused recovery outcome
- **AND** it MUST preserve the connection-limit error without replay, request settlement, or an account health penalty
