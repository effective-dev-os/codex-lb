## ADDED Requirements

### Requirement: Non-streaming Chat reconciles terminal assistant text

For collected non-streaming Chat completions, the service MUST prefer populated `output_text` content from assistant message items in `response.completed` or `response.incomplete` output over accumulated text deltas. It MUST concatenate selected parts in output and content order without duplicating delta text. It MUST retain delta text when terminal assistant output text is empty or absent. It MUST NOT expose reasoning or non-assistant output as message content. Existing refusal, tool-call, incomplete finish-reason, terminal error precedence and usage mapping MUST be preserved.

#### Scenario: Terminal-only assistant text

- **WHEN** a non-streaming Chat request receives terminal assistant output text without text delta events
- **THEN** its Chat message contains that text and preserves terminal usage

#### Scenario: Full or partial deltas precede terminal output

- **WHEN** full or partial text deltas precede populated terminal assistant output text
- **THEN** the Chat content equals the terminal assistant text once, in part order

#### Scenario: No terminal assistant text

- **WHEN** terminal output contains no populated assistant output-text parts
- **THEN** the collector retains text deltas, including a genuinely empty completion when there are no deltas

#### Scenario: Error takes precedence over terminal text

- **WHEN** a terminal error occurs before or after a terminal response containing assistant output text
- **THEN** the collector returns the error envelope instead of successful content
