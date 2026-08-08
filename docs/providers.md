# Providers & models

polyclaude works with any OpenAI-compatible backend. Presets:

| Provider | Flag | Key env | Base URL | Default model |
|---|---|---|---|---|
| Gemini | `--gemini` | `GEMINI_API_KEY` / `GOOGLE_API_KEY` | `generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-pro` |
| OpenAI | `--openai` | `OPENAI_API_KEY` | `api.openai.com/v1` | `gpt-4.1` |
| Groq | `--groq` | `GROQ_API_KEY` | `api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| OpenRouter | `--openrouter` | `OPENROUTER_API_KEY` | `openrouter.ai/api/v1` | `anthropic/claude-3.5-sonnet` |
| Ollama | `--ollama` | — | `127.0.0.1:11434/v1` | `qwen2.5-coder` |

Override the model with `--model NAME`.

## Newer models

These work; polyclaude smooths over their API differences:

- **Gemini 3.x** (`gemini-3.1-pro-preview`, `gemini-pro-latest`, …) — thinking
  models. Their function calls carry a *thought signature* that must be sent
  back on the next turn or the API rejects it; polyclaude stores and replays it.
- **GPT-5.x / 5.5 / 5.6** — on `/v1/chat/completions` these refuse to use tools
  and reasoning at the same time. polyclaude routes `gpt-5.5`/`gpt-5.6` through
  OpenAI's `/v1/responses` API so you get tools **and** full reasoning; control
  depth with `--reasoning low|medium|high` (default `high`). Reasoning items are
  round-tripped across turns.
- **o-series / any reasoning model** — non-default `temperature`/`top_p` are
  dropped (they'd be rejected).

## Tips

- `--reasoning` only affects OpenAI reasoning models routed via `/responses`.
- Free Groq keys have low per-minute token limits; large contexts may 429.
- For a fully local, no-key setup: install Ollama, `ollama pull qwen2.5-coder`,
  then `polyclaude --ollama`.
