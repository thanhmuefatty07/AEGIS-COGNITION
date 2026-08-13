# Browser Skill

## Overview
Use browser commands for physical web observations: navigate, interact, extract,
and record outputs.

## When to Use
Use when the task needs current web page state, form interaction, screenshots, or
link extraction.

## Core Concepts
Keep sessions explicit. Persist cookies deliberately. Bind extracted output into
evidence before memory commit.

## Examples
```rust
page.navigate("https://example.com")?;
page.type_text("#q", "aegis")?;
let text = page.extract_text("#result")?;
```

## Best Practices
Use rate limits, stable selectors, and bounded timeouts.

## Common Pitfalls
Do not assume stealth success without site-specific physical evidence.
