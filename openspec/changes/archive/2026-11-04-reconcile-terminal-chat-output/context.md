# Terminal Chat text reconciliation

For non-streaming requests the gateway still consumes an upstream Responses stream. A completed response with `output: [{type: "message", role: "assistant", content: [{type: "output_text", text: "hello"}]}]` must yield `message.content: "hello"`, even without text deltas. Populated terminal text replaces the accumulated text, including partial deltas. Empty/absent terminal assistant text retains delta fallback. Output order and content-part order are preserved; reasoning and other output types are excluded.

The original Daily upstream events were not retained; this is a reproducible code defect and a plausible incident mechanism, not a proven historical reconstruction. Streaming text reconstruction is outside this change because already-emitted deltas cannot be replaced in the same way.
