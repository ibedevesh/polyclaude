"""Provider presets: base URL, default models, and which env var holds the key.

Defaults are broadly-available models. Override any with `--model NAME`, and
newer models (Gemini 3.x, GPT-5.x/5.6) work too — the bridge handles their
quirks automatically.
"""

PROVIDERS = {
    "claude": {
        # passthrough: no backend — talk to the REAL Anthropic model, with the
        # identity scrub applied on the way out. Uses Claude Code's own auth.
        "base": "",
        "model": "",       # keep whatever model Claude Code requests
        "small": "",
        "key_env": [],     # real auth comes from claude.ai login or ANTHROPIC_API_KEY
        "passthrough": True,
        "help": "Real Anthropic models. Needs a claude.ai login or ANTHROPIC_API_KEY.",
    },
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.5-pro",
        "small": "gemini-2.5-flash",
        "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "help": "Get a key at https://aistudio.google.com/apikey",
    },
    "openai": {
        "base": "https://api.openai.com/v1",
        "model": "gpt-4.1",
        "small": "gpt-4.1-mini",
        "key_env": ["OPENAI_API_KEY"],
        "help": "Get a key at https://platform.openai.com/api-keys",
    },
    "groq": {
        "base": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "small": "llama-3.1-8b-instant",
        "key_env": ["GROQ_API_KEY"],
        "help": "Get a free key at https://console.groq.com/keys",
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-3.5-sonnet",
        "small": "anthropic/claude-3.5-haiku",
        "key_env": ["OPENROUTER_API_KEY"],
        "help": "Get a key at https://openrouter.ai/keys",
    },
    "ollama": {
        "base": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5-coder",
        "small": "qwen2.5-coder",
        "key_env": [],  # local, no key
        "help": "Install from https://ollama.com and `ollama pull qwen2.5-coder`",
    },
}
