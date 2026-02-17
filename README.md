# Virtual Life - Gradio Chat App

## Features
- Gradio-hosted chat UI (text + image upload)
- Enter to send (`Shift+Enter` for newline)
- Toggle to enable/disable reasoning
- OpenRouter model: `google/gemini-3-flash-preview`
- Markdown rendering in chat UI
- Uploaded images are stored locally in `chat_images/` with light JPEG compression

## Memory and History Files
- `history.md`
  - Stores all original conversations (full turn log)
- `history-compressed.md`
  - Stores compressed summaries (every 10 turns)
  - Also includes pending uncompressed turns (<10)
  - Sent to model every request as chat history context
- `memory.md`
  - Stores durable user memory extracted by function-calling (`save_memory_fact`)
  - Sent to model every request as memory context
- `history_state.json`
  - Internal state for compression bookkeeping

## Setup
1. Create virtual env (optional)
   - `python -m venv .venv`
   - `.venv\\Scripts\\activate`
2. Install deps
   - `pip install -r requirements.txt`
3. Create `.env` from `.env.example`
   - `OPENROUTER_API_KEY=...`
   - `APP_AUTH_USERNAME=...`
   - `APP_AUTH_PASSWORD=...`
   - Optional lockout controls:
     - `AUTH_MAX_ATTEMPTS=5`
     - `AUTH_LOCKOUT_SECONDS=300`
     - `TRUSTED_SESSION_DAYS=30`
     - `SESSION_HOURS=12`
     - `COOKIE_SECURE=false`
4. Run
   - `python main.py`
5. Open
   - `http://127.0.0.1:7860`

## Security Notes
- App now runs behind a login gateway at `/login`.
- Trusted device option stores a secure auth cookie (persistent for `TRUSTED_SESSION_DAYS`).
- If not trusted, login requires username/password.
- Non-trusted logins create shorter sessions (`SESSION_HOURS`).
- Repeated failed logins trigger temporary lockout.
- Runs locally by default (`127.0.0.1:7860`).
