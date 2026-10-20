## 1. Completion identity

- [x] Reproduce stable-but-colliding stream and fallback IDs in regression tests.
- [x] Generate a unique ID per stream and non-streaming fallback without changing valid provider IDs.
- [x] Verify mapping/bridge tests, concurrency, tools, terminal usage and native ledger behavior.
- [x] Validate OpenSpec and record the production rollout boundary without claiming deployment.

Original fork evidence (not fresh-main counts): old code fails seven new identity cases; fixed mapping/cleanup48 and Chat route17
checks pass, as does scoped Ruff. Actual old/new adapter SSE through the isolated native
stand reproduces duplicate-ID ledger loss and verifies two fixed rows/key/user/5h/7d counters.
Pinned OpenSpec 1.11.0 validation passes: all 65 canonical specs and the strict change
validator. The owner acquired the exact CI pin in an isolated temporary npm cache;
`chat-ledger-id/openspec-validation.json` records the result. Production deployment/backfill
is not claimed.

Fresh-main candidate validation on 2026-10-20: 45 mapping and actual Chat route tests pass,
scoped Ruff and diff-check pass. The candidate preserves main mapping/error behavior and
valid provider response IDs. Original-fork native accounting evidence above is supporting
evidence, not an execution against the fresh-main tree. Current-head CI and cloud review
remain required before merge.
