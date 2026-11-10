## Why

Direct Responses streams share one environment-configured SOCKS connector across accounts. A typed SOCKS CONNECT timeout currently becomes a generic stream terminal and loses its pre-dispatch provenance; treating that common egress as an account route also permits unnecessary account penalties or reassignment.

## What Changes

- Preserve typed pre-dispatch proxy failures on the shared SOCKS HTTP/WebSocket session as account-neutral process-network errors.
- Reuse existing bounded same-account recovery, generation retirement, continuity and settlement contracts.
- Keep dedicated account proxy routes, ambiguous delivery, and native-helper failures unchanged.

## Capabilities

### Modified Capabilities

- `outbound-http-clients`: Define shared SOCKS connector failure scope and replay eligibility.

## Impact

`app/core/clients/proxy.py`, targeted unit and real Responses route tests. No settings, schema, dependencies, deployment or historical-accounting changes.
