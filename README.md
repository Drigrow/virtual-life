# Virtual Life

> A personal AI companion that remembers — built for long-term, immersive role-play and conversation.

**Virtual Life** is a self-hosted chat app powered by [OpenRouter](https://openrouter.ai) (default model: `google/gemini-3-flash-preview`). The name reflects the idea of a *second life* running alongside yours: an AI that accumulates memory over time, knows who you are, and stays consistent across every conversation — like a persistent virtual relationship.

---

## Why "Virtual Life"?

Most AI chats are stateless — every session starts fresh. Virtual Life is different:

- 🧠 **It remembers you.** Every conversation is logged and progressively compressed into long-term memory. The AI always has context from past sessions.
- 🎭 **It plays a role.** You define a persistent user profile (`user.md`) — your identity, persona, preferences, and boundaries — and the AI respects it every time.
- 📖 **It builds a history.** Raw chat logs, compressed summaries, and extracted memory facts all accumulate over time, forming a living record of your virtual relationship.

---

## Features

| Feature | Details |
|---|---|
| 💬 Chat UI | Gradio-based, supports text + image upload |
| 🖼️ Image support | Upload images; stored locally with JPEG compression |
| 🧠 Long-term memory | Turns auto-compress every 10 chats; prior context informs each new compression |
| ✍️ Manual Compress | Force-compress pending turns at any time |
| ⚡ Advanced Compress | Re-chunk and merge compressed summaries to shrink memory size (with confirmation guard) |
| 🔍 Web search | Optionally enabled; triggers automatically on time-sensitive queries |
| 💡 Thinking mode | Toggle extended reasoning on/off |
| 👤 User profile | Editable `user.md` — persistent identity/persona for the AI |
| 🔒 Auth gateway | Login-protected with trusted device cookies and brute-force lockout |
| 🌐 i18n | English and Simplified Chinese UI |

---

## Memory System

```
history.md              ← Full raw chat log (every turn, never deleted automatically)
history-compressed.md   ← Compressed summaries + pending uncompressed turns
                           Sent to the model as context every request
memory.md               ← Durable facts extracted by function-calling (preferences, goals, profile)
                           Sent to the model as memory every request
user.md                 ← Your persistent identity/persona (editable in the UI)
history_state.json      ← Internal bookkeeping for compression state
```

**Compression flow:**
1. Every 10 turns → auto-compress into a new summary (aware of prior summaries for continuity)
2. **Manual Compress** → compress pending turns immediately, regardless of count
3. **Advanced Compress** → re-chunk existing summaries (5 per chunk, 1st summary preserved) and re-summarize to reduce file size

---

## Setup

```bash
# 1. (Optional) Create a virtual environment
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and fill in your values

# 4. Run
python main.py
```

Then open **http://127.0.0.1:7860** in your browser.

---

## Configuration (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | ✅ | — | Your OpenRouter API key |
| `APP_AUTH_USERNAME` | ✅ | — | Login username |
| `APP_AUTH_PASSWORD` | ✅ | — | Login password |
| `AUTH_MAX_ATTEMPTS` | ❌ | `5` | Failed logins before lockout |
| `AUTH_LOCKOUT_SECONDS` | ❌ | `300` | Lockout duration (seconds) |
| `TRUSTED_SESSION_DAYS` | ❌ | `30` | Trusted device cookie lifetime |
| `SESSION_HOURS` | ❌ | `12` | Non-trusted session lifetime |
| `COOKIE_SECURE` | ❌ | `false` | Set `true` if serving over HTTPS |

---

## Security Notes

- All personal data files (`.env`, `history.md`, `memory.md`, `user.md`, `trusted_devices.json`, `chat_images/`) are excluded from git via `.gitignore`.
- The app runs behind a login gateway at `/login` with HMAC-secured session tokens.
- Trusted device option stores a persistent secure cookie so you don't re-enter credentials on every visit.
- Repeated failed logins trigger a temporary lockout.
- Runs locally on `127.0.0.1` by default — not exposed to the internet unless you explicitly configure it.
