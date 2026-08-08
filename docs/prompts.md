# Specializing the system prompt

polyclaude can inject a persona / use-case into the agent's system prompt so the
same Claude Code becomes a specialized assistant — without editing anything in
Claude Code itself.

## Profiles

```bash
polyclaude --gemini --profile datascience
polyclaude --openai --profile reviewer
polyclaude --gemini --profile concise
```

The profile text is **appended** to the system prompt of the **main agent
loop** only. The lightweight side-calls Claude Code makes (conversation titles,
summaries, etc.) are left alone, so nothing breaks.

Bundled profiles: `datascience`, `reviewer`, `concise`.

## Your own profile

Point `--profile` at any Markdown file:

```bash
polyclaude --gemini --profile ./profiles/backend.md
```

A profile is just plain instructions, e.g.:

```markdown
# Backend engineer
You work on a Python + FastAPI + Postgres service. Prefer async, type hints,
and small pure functions. Always add or update tests for changed behavior.
```

## Replace the whole system prompt

For full control:

```bash
polyclaude --gemini --system ./my-system.md
```

This replaces the main system prompt entirely. Note that Claude Code's built-in
prompt carries important tool-usage contracts — replacing it wholesale can
change behavior, so append (a profile) unless you know you want a full swap.
