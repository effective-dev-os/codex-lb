# Recover upstream WebSocket connection lifetime limits

An upstream `websocket_connection_limit_reached` error retires one physical connection. Treating it as a missing previous response changes continuity classification and can migrate a retry to another account.

Recover reactively on a fresh socket belonging to the same account when the pending request is safe to replay before meaningful output. Keep a single replay budget, preserve settlement and admission ownership, and expose a distinct recovery metric and log. Do not add a connection-age timer.
