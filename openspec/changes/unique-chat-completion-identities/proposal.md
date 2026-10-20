## Why

Chat conversion emits the same `chatcmpl_temp` response ID for independent completions.
Downstream accounting that uses response IDs as unique ledger identities silently discards
later successful ledger records. Non-streaming responses without a provider ID have the same collision.

## What Changes

- Allocate one unique Chat completion ID per stream state and retain it across content, tool,
  finish and usage chunks, including the asynchronous per-line adapter.
- Generate a unique non-streaming fallback when the upstream supplies no usable ID; preserve
  existing non-empty provider response IDs.
- Cover sequential/concurrent identities and the native duplicate-ID failure separately.

## Capabilities

### Modified Capabilities

- `chat-completions-compat`: stable per-completion identities across converted streams and unique fallback identities.

## Impact

- Affected spec: `chat-completions-compat`.
- Affected code: `app/core/openai/chat_responses.py` and response mapping tests.
- No database schema, transport retry, authentication, pricing or historical backfill changes.
