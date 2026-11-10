# Change: Reconcile terminal Chat completion output

## Why

The non-streaming Chat collector reads text deltas but ignores terminal Responses output. A terminal response containing assistant output text can therefore produce an empty successful Chat completion. The historical Daily event stream is unavailable, so the incident mechanism remains unproven.

## What Changes

Use populated assistant output-text content from completed or incomplete terminal responses as the authoritative collected text. Preserve delta fallback, refusal/tool/error/usage behavior and existing streaming behavior.

## Capabilities

### Modified Capabilities

- `chat-completions-compat`: reconcile terminal text for non-streaming collection.

## Impact

Only Chat response collection and focused tests/specs; no settings, dependencies, persistence or deployment changes.
