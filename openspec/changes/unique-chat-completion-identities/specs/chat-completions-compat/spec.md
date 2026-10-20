## ADDED Requirements

### Requirement: Chat completion identities are unique per completion

The service MUST allocate one unique completion ID per converted Chat stream. Every content,
refusal, tool, finish and usage chunk for that stream MUST reuse the same ID, including when
conversion receives one upstream line at a time. Independent sequential and concurrent streams
MUST have distinct completion IDs even when the upstream payloads are identical.

#### Scenario: Separate streams preserve stable distinct identities

- **WHEN** two independent streams convert identical content/tool events and a terminal event
- **THEN** every chunk within each stream has one stable ID
- **AND** the two streams have different IDs, including their final usage chunks

### Requirement: Non-streaming fallback identities are unique

When a collected non-streaming response has no non-empty upstream response ID, the service
MUST generate a unique Chat completion ID for that completion. A non-empty upstream response
ID MUST be preserved.

#### Scenario: Missing upstream IDs do not collide

- **WHEN** two collected completions have missing or empty upstream response IDs
- **THEN** the returned Chat completion IDs are distinct

#### Scenario: Valid upstream identity is preserved

- **WHEN** a collected completion includes a non-empty upstream response ID
- **THEN** the returned Chat completion retains that exact ID
