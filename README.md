# polyclaude

**Use OpenAI, Gemini, or any OpenAI-compatible model inside the Claude Code CLI.**

Claude Code is a great terminal coding agent — but it only talks to Anthropic's
models. `polyclaude` lets you keep the exact Claude Code experience (its UI,
tools, and agent loop) while the actual model behind it is **GPT, Gemini, a Groq
model, an OpenRouter model, or a local Ollama model**.

It works by running a tiny local proxy that speaks Claude Code's API on one side
and your provider's API on the other, translating between them on the fly.
Claude Code itself is never modified.

```
Claude Code  ⇄  Anthropic Messages API  ⇄  [ polyclaude ]  ⇄  OpenAI / Gemini / …
```

---

## Install

```bash
pipx install polyclaude      # recommended
# or: uv tool install polyclaude
# or: pip install polyclaude
```

You'll also need [Claude Code](https://docs.claude.com/claude-code) installed
(`claude` on your PATH) and an API key for whichever provider you want to use.

## Use

```bash
export GEMINI_API_KEY=...        # or OPENAI_API_KEY, GROQ_API_KEY, …
polyclaude --gemini              # Claude Code, powered by Gemini
polyclaude --openai              # …powered by GPT
polyclaude --openai --model gpt-4.1
polyclaude --gemini --model gemini-2.5-pro
```

That's it — you're dropped into a normal Claude Code session running on the
model you chose. First run auto-configures the local proxy certificate; nothing
else to set up.

Pass arguments straight through to `claude` after `--`:

```bash
polyclaude --gemini -- -p "explain this repo"
```

---

## Providers

| Provider | Flag | Key env | Default model |
|---|---|---|---|
| Google Gemini | `--gemini` | `GEMINI_API_KEY` | `gemini-2.5-pro` |
| OpenAI | `--openai` | `OPENAI_API_KEY` | `gpt-4.1` |
| Groq | `--groq` | `GROQ_API_KEY` | `llama-3.3-70b-versatile` |
| OpenRouter | `--openrouter` | `OPENROUTER_API_KEY` | `anthropic/claude-3.5-sonnet` |
| Ollama (local) | `--ollama` | — | `qwen2.5-coder` |

Any model your key can access works via `--model`, including the newest ones
(Gemini 3.x, GPT-5.x). polyclaude handles the provider-specific details so tool
calling and reasoning keep working:

- **Gemini 3.x** thinking models need their function-call *thought signatures*
  round-tripped across turns — handled automatically.
- **GPT-5.5 / 5.6** can't use tools and reasoning together on the standard
  endpoint; polyclaude routes them through OpenAI's `/responses` API so you get
  **both** (tune depth with `--reasoning low|medium|high`).
- Reasoning models reject non-default sampling params — dropped automatically.
- Turns that hit the output-token cap are transparently continued.

Run `polyclaude --list` to see everything.

---

## Specialize the agent

Give the agent a persona/use-case with a profile — it's appended to the system
prompt for the main loop only, so Claude Code's own tooling keeps working:

```bash
polyclaude --gemini --profile datascience
polyclaude --openai --profile reviewer
polyclaude --gemini --profile ./my-profile.md      # your own file
```

Bundled profiles: `datascience`, `reviewer`, `concise`. Or replace the whole
system prompt with `--system path/to/file.md`.

---

## Resume a session

Claude Code keeps your conversations; resume them straight through polyclaude:

```bash
polyclaude --gemini --continue     # continue the most recent session
polyclaude --gemini --resume       # pick one from a list
```

## Reskin (optional, cosmetic)

By default the welcome screen still reads "Claude Code" and shows a Claude model
name even though another model is answering. `--reskin` recolors the UI, relabels
it to **polyclaude**, and shows the **real** model name:

```bash
polyclaude --gemini --reskin              # green accent + polyclaude label
polyclaude --openai --reskin --hue 200    # a different accent colour
```

This is display-only — it rewrites the terminal output as it's drawn; the Claude
Code binary is never modified and your keystrokes pass through untouched.

## How it works

`polyclaude` starts [mitmproxy](https://mitmproxy.org) locally with a small
addon and points Claude Code at it (via `HTTPS_PROXY` + `NODE_EXTRA_CA_CERTS`,
scoped to that one process). The addon intercepts requests to
`api.anthropic.com/v1/messages`, translates the Anthropic request into the
provider's format, calls the provider, and streams the reply back in Anthropic's
event format. Every other host is passed straight through untouched.

Only sessions you launch with `polyclaude` are affected — your normal `claude`
is unchanged and still uses Anthropic.

---

## FAQ

**Does this change my Claude Code install?** No. It only sets proxy environment
variables for the session it launches.

**Do my keys leave my machine?** Only to the provider you choose (Google,
OpenAI, etc.), exactly as if you called their API directly. polyclaude has no
servers.

**Quality feels off on some models.** Different models behave differently inside
Claude Code's harness (its prompt and tools are tuned for Claude). Larger/newer
models fare better; try `--reasoning high` on OpenAI and compare providers.

**Can I use my own endpoint?** Yes — anything OpenAI-compatible. Point at it and
go (OpenRouter, vLLM, LM Studio, etc.).

---

## Notes

For personal and development use, with your own API keys. Respect the terms of
service of Claude Code and of whichever model provider you use.

## License

MIT © Devesh Pratap
