import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import asyncio
import collections
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn
from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from openai import AuthenticationError, OpenAI
from PIL import Image

load_dotenv()

# Switchable backend models (WebUI dropdown; selection persists in model_state.json)
MODEL_CHOICES = [
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.5-flash-lite",
    "google/gemini-3.7-flash",
    "openai/gpt-5.6-luna",
    "openai/gpt-oss-120b",
    "x-ai/grok-4.20",
]
DEFAULT_MODEL = MODEL_CHOICES[0]

# Per-model reasoning handling on OpenRouter:
#   "off"     -> reasoning is optional; disable it (reasoning.enabled=false)
#   "minimal" -> reasoning is mandatory; use minimal effort
#   "low"     -> reasoning is mandatory; use the lowest supported effort
MODEL_REASONING = {
    "google/gemini-3.1-flash-lite-preview": "off",
    "google/gemini-3.5-flash-lite": "minimal",
    "google/gemini-3.7-flash": "low",
    "openai/gpt-5.6-luna": "off",
    "openai/gpt-oss-120b": "low",
    "x-ai/grok-4.20": "off",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024
COMPRESS_EVERY_TURNS = 10
MAX_UI_TURNS = 50  # How many recent turns /api/init returns to the browser
COMPRESS_SIZE_THRESHOLD = 3000  # Rolling-compress pending text when it reaches ~3000 chars
MAX_SUMMARY_CHARS = 8000  # Safety cap for a single rolling summary
FACTS_EAGER_TURNS = 4  # Eagerly extract facts once this many pending turns accumulate
MEMORY_SIZE_BUDGET = 8000  # Target max chars for memory.md
AUTH_MAX_ATTEMPTS = int(os.getenv("AUTH_MAX_ATTEMPTS", "5"))
AUTH_LOCKOUT_SECONDS = int(os.getenv("AUTH_LOCKOUT_SECONDS", "300"))
TRUSTED_SESSION_DAYS = int(os.getenv("TRUSTED_SESSION_DAYS", "30"))
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "12"))
COOKIE_NAME = "vl_auth"
COOKIE_SECURE = str(os.getenv("COOKIE_SECURE", "false")).lower() in ("1", "true", "yes")

STATE_PATH = Path("history_state.json")
HISTORY_MD_PATH = Path("history.md")
HISTORY_COMPRESSED_MD_PATH = Path("history-compressed.md")
MEMORY_MD_PATH = Path("memory.md")
USER_MD_PATH = Path("user.md")
IMAGE_DIR = Path("chat_images")
TRUSTED_DEVICES_PATH = Path("trusted_devices.json")
MODEL_STATE_PATH = Path("model_state.json")
_AUTH_LOCK = threading.Lock()
_AUTH_STATE: dict[str, dict[str, float | int]] = {}
_STATE_LOCK = threading.Lock()
_MEMORY_LOCK = threading.Lock()

TURN_PATTERN = re.compile(
    r"## Turn \((?P<ts>.*?)\)\n"
    r"<!-- USER_START -->\n(?P<user>.*?)\n<!-- USER_END -->\n"
    r"<!-- ASSISTANT_START -->\n(?P<assistant>.*?)\n<!-- ASSISTANT_END -->\n"
    r"<!-- IMAGE_PATH: (?P<image>.*?) -->",
    re.DOTALL,
)

LEGACY_TURN_PATTERN = re.compile(
    r"## Turn \((?P<ts>.*?)\)\s*\n\n\*\*User\*\*\s*\n\n(?P<user>.*?)\s*\n\n\*\*Assistant\*\*\s*\n\n(?P<assistant>.*?)(?=\n## Turn \(|\Z)",
    re.DOTALL,
)

# I18N and CSS moved to frontend.



def resolve_openrouter_api_key() -> str:
    # Prefer local .env to avoid stale system/shell environment values.
    env_file_key = str(dotenv_values(".env").get("OPENROUTER_API_KEY", "") or "").strip()
    if env_file_key:
        return env_file_key.strip().strip("\"").strip("'")

    env_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    return env_key.strip().strip("\"").strip("'")


def resolve_auth_credential(name: str) -> str:
    env_file_val = str(dotenv_values(".env").get(name, "") or "").strip()
    if env_file_val:
        return env_file_val.strip().strip("\"").strip("'")
    return str(os.getenv(name, "") or "").strip().strip("\"").strip("'")


def auth_guard(username: str, password: str) -> bool:
    expected_user = resolve_auth_credential("APP_AUTH_USERNAME")
    expected_pass = resolve_auth_credential("APP_AUTH_PASSWORD")
    if not expected_user or not expected_pass:
        return False

    now = time.time()
    key = username or "<empty>"
    with _AUTH_LOCK:
        state = _AUTH_STATE.get(key, {"failures": 0, "locked_until": 0.0})
        locked_until = float(state.get("locked_until", 0.0))
        if now < locked_until:
            return False

        ok = hmac.compare_digest(username, expected_user) and hmac.compare_digest(
            password, expected_pass
        )
        if ok:
            _AUTH_STATE.pop(key, None)
            return True

        failures = int(state.get("failures", 0)) + 1
        new_state: dict[str, float | int] = {"failures": failures, "locked_until": 0.0}
        if failures >= max(1, AUTH_MAX_ATTEMPTS):
            new_state["locked_until"] = now + max(1, AUTH_LOCKOUT_SECONDS)
            new_state["failures"] = 0
        _AUTH_STATE[key] = new_state
        return False


def validate_auth_config() -> None:
    user = resolve_auth_credential("APP_AUTH_USERNAME")
    pw = resolve_auth_credential("APP_AUTH_PASSWORD")
    if not user or not pw:
        raise RuntimeError(
            "Missing APP_AUTH_USERNAME / APP_AUTH_PASSWORD. Set them in .env before launch."
        )


def load_trusted_store() -> dict:
    ensure_files()
    try:
        store = json.loads(TRUSTED_DEVICES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        store = {"tokens": {}}
    store.setdefault("tokens", {})
    return store


def save_trusted_store(store: dict) -> None:
    TRUSTED_DEVICES_PATH.write_text(json.dumps(store, ensure_ascii=True, indent=2), encoding="utf-8")


def cleanup_expired_tokens(store: dict | None = None) -> dict:
    store = store or load_trusted_store()
    now = time.time()
    tokens = store.get("tokens", {})
    alive = {k: v for k, v in tokens.items() if float(v.get("expires_at", 0.0)) > now}
    if len(alive) != len(tokens):
        store["tokens"] = alive
        save_trusted_store(store)
    return store


def issue_session_token(username: str, user_agent: str, trust_device: bool) -> tuple[str, int]:
    ttl_seconds = TRUSTED_SESSION_DAYS * 24 * 3600 if trust_device else SESSION_HOURS * 3600
    raw_token = secrets.token_urlsafe(48)
    token_id = secrets.token_hex(16)
    raw_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = time.time() + max(60, ttl_seconds)

    store = cleanup_expired_tokens()
    store["tokens"][token_id] = {
        "username": username,
        "created_at": time.time(),
        "expires_at": expires_at,
        "trusted": bool(trust_device),
        "user_agent": (user_agent or "")[:200],
        "raw_hash": raw_hash,
    }
    save_trusted_store(store)
    return f"{token_id}.{raw_token}", ttl_seconds


def validate_session_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    token_id, raw = token.split(".", 1)
    store = cleanup_expired_tokens()
    entry = store.get("tokens", {}).get(token_id)
    if not entry:
        return None
    expected_raw_hash = str(entry.get("raw_hash", ""))
    actual_raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_raw_hash, expected_raw_hash):
        return None
    if float(entry.get("expires_at", 0.0)) <= time.time():
        return None
    return str(entry.get("username", "")) or None


def revoke_session_token(token: str | None) -> None:
    if not token or "." not in token:
        return
    token_id, _raw = token.split(".", 1)
    store = load_trusted_store()
    if token_id in store.get("tokens", {}):
        store["tokens"].pop(token_id, None)
        save_trusted_store(store)


def auth_dependency(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    return validate_session_token(token)


def login_page_html(error: str = "") -> str:
    err = f"<p style='color:#b00020;font-weight:600'>{error}</p>" if error else ""
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Virtual Life Login</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; background:#f5f6f8; margin:0; }}
    .card {{ max-width:420px; margin:8vh auto; background:white; border:1px solid #ddd; border-radius:14px; padding:22px; }}
    h1 {{ margin-top:0; font-size:22px; }}
    label {{ display:block; margin-top:10px; font-weight:600; }}
    input[type=text], input[type=password] {{ width:100%; padding:10px; margin-top:6px; border:1px solid #ccc; border-radius:8px; }}
    .row {{ margin-top:12px; }}
    button {{ margin-top:14px; width:100%; padding:11px; border:0; border-radius:10px; background:#145a7a; color:white; font-weight:700; }}
    .note {{ color:#555; font-size:13px; margin-top:10px; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Login Required</h1>
    {err}
    <form method="post" action="/login">
      <label>Username</label>
      <input type="text" name="username" autocomplete="username" required />
      <label>Password</label>
      <input type="password" name="password" autocomplete="current-password" required />
      <div class="row">
        <input type="checkbox" id="trust_device" name="trust_device" />
        <label for="trust_device" style="display:inline;font-weight:500;">Trust this device for {TRUSTED_SESSION_DAYS} days</label>
      </div>
      <button type="submit">Sign in</button>
    </form>
    <div class="note">Trusted devices use a secure cookie so you do not enter password every opening.</div>
  </div>
</body>
</html>"""


def get_client() -> OpenAI:
    api_key = resolve_openrouter_api_key()
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY. Set it in your environment or .env file.")

    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


# Language detection removed as I18N was removed in favor of HTML bindings.


def ensure_files() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    if not STATE_PATH.exists():
        save_state({"compressed_memories": [], "pending_turns": [], "rolling_summary": "", "facts_watermark": 0})

    if not HISTORY_MD_PATH.exists():
        HISTORY_MD_PATH.write_text("# Chat History\n\n", encoding="utf-8")

    if not HISTORY_COMPRESSED_MD_PATH.exists():
        HISTORY_COMPRESSED_MD_PATH.write_text("# History Compressed\n\n", encoding="utf-8")

    if not MEMORY_MD_PATH.exists():
        MEMORY_MD_PATH.write_text("# Memory\n\n", encoding="utf-8")

    if not USER_MD_PATH.exists():
        USER_MD_PATH.write_text(
            "# User\n\nWho you are notes for role-play identity, background, tone, and boundaries.\n",
            encoding="utf-8",
        )

    if not TRUSTED_DEVICES_PATH.exists():
        TRUSTED_DEVICES_PATH.write_text(json.dumps({"tokens": {}}, indent=2), encoding="utf-8")

    if not MODEL_STATE_PATH.exists():
        MODEL_STATE_PATH.write_text(json.dumps({"model": DEFAULT_MODEL}, indent=2), encoding="utf-8")


def load_model_state() -> str:
    ensure_files()
    try:
        data = json.loads(MODEL_STATE_PATH.read_text(encoding="utf-8"))
        model = str(data.get("model", "") or "")
        if model in MODEL_CHOICES:
            return model
    except (json.JSONDecodeError, OSError):
        pass
    return DEFAULT_MODEL


def save_model_state(model_id: str) -> bool:
    model_id = (model_id or "").strip()
    if model_id not in MODEL_CHOICES:
        return False
    MODEL_STATE_PATH.write_text(json.dumps({"model": model_id}, indent=2), encoding="utf-8")
    return True


def get_current_model() -> str:
    return load_model_state()


def model_extra_body(model: str) -> dict:
    """Reasoning params for a model: disable when optional, lowest effort when mandatory."""
    reasoning = MODEL_REASONING.get(model)
    if reasoning == "off":
        return {"reasoning": {"enabled": False}}
    if reasoning in ("minimal", "low"):
        return {"reasoning_effort": reasoning}
    return {}


def load_state() -> dict:
    ensure_files()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {"compressed_memories": [], "pending_turns": [], "rolling_summary": "", "facts_watermark": 0}
    if not isinstance(state, dict):
        state = {"compressed_memories": [], "pending_turns": [], "rolling_summary": "", "facts_watermark": 0}

    state.setdefault("compressed_memories", [])
    state.setdefault("pending_turns", [])
    state.setdefault("rolling_summary", "")
    state.setdefault("facts_watermark", 0)
    if not isinstance(state["compressed_memories"], list):
        state["compressed_memories"] = []
    if not isinstance(state["pending_turns"], list):
        state["pending_turns"] = []
    if not isinstance(state["rolling_summary"], str):
        state["rolling_summary"] = ""
    if not isinstance(state["facts_watermark"], int) or isinstance(state["facts_watermark"], bool):
        state["facts_watermark"] = 0

    cleaned_pending = []
    for turn in state["pending_turns"]:
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user", "") or "").strip()
        assistant = str(turn.get("assistant", "") or "").strip()
        if user or assistant:
            cleaned_pending.append({"user": user, "assistant": assistant})
    state["pending_turns"] = cleaned_pending
    state["facts_watermark"] = min(max(0, int(state["facts_watermark"])), len(cleaned_pending))

    cleaned_comp = []
    for item in state["compressed_memories"]:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        summary = str(item.get("summary", "") or "").strip()
        if isinstance(sid, int) and summary:
            cleaned_comp.append({"id": sid, "summary": summary})
    state["compressed_memories"] = cleaned_comp

    # One-time migration: fold all legacy compressed summaries into a single
    # rolling_summary, then drop the legacy list so it is never re-joined.
    if state["compressed_memories"] and not state["rolling_summary"].strip():
        parts = [item["summary"] for item in state["compressed_memories"] if item["summary"].strip()]
        if parts:
            state["rolling_summary"] = "\n\n---\n\n".join(parts)
        state["compressed_memories"] = []
        save_state(state)
    return state


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=True, indent=2), encoding="utf-8")


def file_to_data_url(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    mime_type = guessed or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def persist_and_compress_image(temp_image_path: str) -> Path:
    src = Path(temp_image_path)
    if not src.exists():
        raise ValueError("Uploaded image file was not found.")

    if src.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image too large. Max size is 8 MB.")

    out_path = IMAGE_DIR / f"img_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}.jpg"

    with Image.open(src) as img:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        img.thumbnail((1600, 1600))
        img.save(out_path, format="JPEG", quality=82, optimize=True)

    return out_path


def build_user_message(prompt: str, image_path: str | None) -> tuple[dict, str, str, str | None]:
    prompt = (prompt or "").strip()

    if image_path:
        saved_image = persist_and_compress_image(image_path)
        data_url = file_to_data_url(saved_image)

        content = []
        if prompt:
            content.append({"type": "text", "text": prompt})
        else:
            content.append({"type": "text", "text": "Describe this image."})
        content.append({"type": "image_url", "image_url": {"url": data_url}})

        user_plain = prompt if prompt else "Describe this image."
        display_text = user_plain + "\n\n" + f"![uploaded image]({data_url})"
        memory_user_text = f"{user_plain}\n[Image attached: {saved_image.as_posix()}]"

        return {"role": "user", "content": content}, memory_user_text, user_plain, saved_image.as_posix()

    if not prompt:
        raise ValueError("Please enter a message or upload an image.")

    return {"role": "user", "content": prompt}, prompt, prompt, None


def append_turn_history(user_plain: str, assistant_text: str, image_path: str | None) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    img = image_path or ""
    with HISTORY_MD_PATH.open("a", encoding="utf-8") as f:
        f.write(f"## Turn ({now})\n")
        f.write("<!-- USER_START -->\n")
        f.write(f"{user_plain}\n")
        f.write("<!-- USER_END -->\n")
        f.write("<!-- ASSISTANT_START -->\n")
        f.write(f"{assistant_text}\n")
        f.write("<!-- ASSISTANT_END -->\n")
        f.write(f"<!-- IMAGE_PATH: {img} -->\n\n")


def load_chat_history_for_ui(max_turns: int | None = None) -> list[dict]:
    ensure_files()
    text = HISTORY_MD_PATH.read_text(encoding="utf-8")
    turns = []

    marker_matches = list(TURN_PATTERN.finditer(text))
    if marker_matches:
        for m in marker_matches:
            user_plain = m.group("user").strip()
            assistant = m.group("assistant").strip()
            image_path = m.group("image").strip()

            if image_path:
                img_file = Path(image_path)
                if img_file.exists():
                    user_content = f"{user_plain}\n\n![uploaded image](/images/{img_file.name})"
                else:
                    user_content = f"{user_plain}\n\n[Image missing: {image_path}]"
            else:
                user_content = user_plain

            turns.append({"role": "user", "content": user_content})
            turns.append({"role": "assistant", "content": assistant})
        if max_turns is not None:
            # Keep the most recent `max_turns` conversation turns (2 messages each).
            turns = turns[-max_turns * 2 :]
        return turns

    legacy_matches = list(LEGACY_TURN_PATTERN.finditer(text))
    for m in legacy_matches:
        user_plain = m.group("user").strip()
        assistant = m.group("assistant").strip()
        turns.append({"role": "user", "content": user_plain})
        turns.append({"role": "assistant", "content": assistant})
    if max_turns is not None:
        turns = turns[-max_turns * 2 :]

    return turns


def init_chat_ui() -> dict:
    initial = load_chat_history_for_ui(max_turns=MAX_UI_TURNS)
    return {"chat_ui_state": initial, "status": f"Loaded {len(initial) // 2} turns from history.md"}


def load_user_md() -> str:
    ensure_files()
    return USER_MD_PATH.read_text(encoding="utf-8")


def save_user_md(content: str) -> dict:
    ensure_files()
    USER_MD_PATH.write_text((content or "").strip() + "\n", encoding="utf-8")
    return {"status": f"Saved user profile to `{USER_MD_PATH.as_posix()}`."}


def clear_all_history(confirm_text: str) -> dict:
    required = "CLEAR ALL HISTORY"
    if (confirm_text or "").strip() != required:
        return {"cleared": False, "status": f"Blocked. Type exactly `{required}` to confirm irreversible deletion."}

    empty_state = {"compressed_memories": [], "pending_turns": [], "rolling_summary": "", "facts_watermark": 0}
    save_state(empty_state)

    HISTORY_MD_PATH.write_text("# Chat History\n\n", encoding="utf-8")
    rewrite_history_compressed(empty_state)
    MEMORY_MD_PATH.write_text("# Memory\n\n", encoding="utf-8")

    if IMAGE_DIR.exists():
        for p in IMAGE_DIR.iterdir():
            if p.is_file():
                p.unlink()

    return {"cleared": True, "status": "History cleared permanently: all conversations, memory, compressed history, and saved images were deleted."}


def estimate_text_size(text: str) -> int:
    # Loose budget: character count is a fine proxy for token/size pressure.
    return len(text or "")


def _strip_json_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def rolling_summarize(client: OpenAI, rolling_summary: str, batch: list[dict]) -> dict:
    """Merge (old rolling summary + new batch) into one updated summary + new facts."""
    turns_text = []
    for idx, turn in enumerate(batch, start=1):
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user", "") or "")
        assistant = str(turn.get("assistant", "") or "")
        turns_text.append(f"Turn {idx}\nUser: {user}\nAssistant: {assistant}")

    if not turns_text:
        return {"summary": rolling_summary, "facts": []}

    prompt = (
        "You maintain a rolling summary of a long-running conversation.\n"
        "Below is the existing rolling summary and a new batch of turns.\n\n"
        "Produce an UPDATED single rolling summary that:\n"
        "- Keeps recent facts, open goals, and anything still relevant.\n"
        "- Drops resolved, expired, and one-off items.\n"
        "- Does NOT restate the user persona/identity (that lives separately in user.md);\n"
        "  only record new facts about the user beyond that persona.\n"
        "- Deduplicates; do not repeat the same information.\n\n"
        "Separately, list NEW durable long-term facts about the user worth persisting\n"
        "(stable preferences, health, long-term goals). Do NOT include one-off requests.\n\n"
        "Respond with ONLY a JSON object, no markdown fences, no commentary, shaped like:\n"
        '{"summary": "...", "facts": ["...", "..."]}\n\n'
        "Existing rolling summary:\n"
        f"{rolling_summary or '(none)'}\n\n"
        "New turns:\n"
        + "\n\n".join(turns_text)
    )

    response = client.chat.completions.create(
        model=get_current_model(),
        messages=[{"role": "user", "content": prompt}],
        extra_body=model_extra_body(get_current_model()),
    )
    msg = response.choices[0].message if response and response.choices else None
    content = (getattr(msg, "content", "") or "").strip() if msg else ""

    summary = ""
    facts: list[str] = []
    try:
        data = json.loads(_strip_json_fences(content))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        raw_summary = str(data.get("summary", "") or "").strip()
        raw_facts = data.get("facts", [])
        if isinstance(raw_facts, list):
            facts = [str(f).strip() for f in raw_facts if str(f).strip()]
        summary = raw_summary or content
    except Exception:
        # Degrade gracefully: keep raw response as summary, no facts.
        summary = content
        facts = []

    summary = (summary or rolling_summary or "").strip()
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS]
    return {"summary": summary, "facts": facts}


def extract_facts_only(client: OpenAI, turns: list[dict]) -> list[str]:
    """Lightweight facts-only extraction used before the compression threshold."""
    turns_text = []
    for idx, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user", "") or "")
        assistant = str(turn.get("assistant", "") or "")
        turns_text.append(f"Turn {idx}\nUser: {user}\nAssistant: {assistant}")

    if not turns_text:
        return []

    prompt = (
        "These are recent conversation turns.\n"
        "Extract any NEW durable long-term facts about the user worth remembering "
        "across sessions (stable preferences, health, long-term goals, identity).\n"
        "Do NOT include one-off requests, trivial events, or persona boilerplate.\n"
        'Respond with ONLY a JSON array of strings, e.g. ["fact1", "fact2"].\n'
        "If nothing is worth saving, respond with [].\n\n"
        + "\n\n".join(turns_text)
    )

    response = client.chat.completions.create(
        model=get_current_model(),
        messages=[{"role": "user", "content": prompt}],
        extra_body=model_extra_body(get_current_model()),
    )
    msg = response.choices[0].message if response and response.choices else None
    content = (getattr(msg, "content", "") or "").strip() if msg else ""

    try:
        data = json.loads(_strip_json_fences(content))
        if isinstance(data, list):
            return [str(f).strip() for f in data if str(f).strip()]
    except Exception:
        pass
    return []


def rewrite_history_compressed(state: dict) -> None:
    lines = [
        "# History Compressed",
        "",
        "Rolling summary of older conversation, plus recent uncompressed turns.",
        "",
    ]

    rolling = str(state.get("rolling_summary", "") or "").strip()
    lines.append("## Rolling Summary")
    lines.append("")
    if rolling:
        lines.append(rolling)
    else:
        lines.append("(none)")
    lines.append("")

    # Show ALL pending turns: the list is already bounded by the compression
    # trigger (>= COMPRESS_EVERY_TURNS turns or >= COMPRESS_SIZE_THRESHOLD
    # chars), so nothing recent is ever hidden from the model.
    pending = state.get("pending_turns", []) or []
    lines.append("## Pending Turns (Uncompressed)")
    lines.append("")
    if pending:
        for idx, turn in enumerate(pending, start=1):
            lines.append(f"### Turn {idx}")
            lines.append("")
            lines.append(f"User: {turn['user']}")
            lines.append("")
            lines.append(f"Assistant: {turn['assistant']}")
            lines.append("")
    else:
        lines.append("(none)")
        lines.append("")

    HISTORY_COMPRESSED_MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def manual_compress() -> dict:
    """Force one rolling compression of all pending turns regardless of threshold."""
    try:
        ensure_files()
        with _STATE_LOCK:
            state = load_state()
            pending = list(state.get("pending_turns", []) or [])
            rolling_summary = str(state.get("rolling_summary", "") or "")
        if not pending:
            return {"status": "Nothing to compress — no pending turns."}

        client = get_client()
        result = rolling_summarize(client, rolling_summary, pending)

        with _STATE_LOCK:
            state = load_state()
            if state.get("pending_turns", []) == pending:
                state["rolling_summary"] = result.get("summary", "")
                state["pending_turns"] = []
                state["facts_watermark"] = 0
                save_state(state)
                rewrite_history_compressed(state)
            else:
                return {"status": "Pending turns changed during compression — please retry."}

        facts = result.get("facts") or []
        if facts:
            try:
                merged = merge_memory_with_model(client, facts)
            except Exception as exc:
                return {"status": f"Compressed {len(pending)} turn(s) into the rolling summary, but memory merge failed: {exc}"}
            if not merged:
                return {"status": f"Compressed {len(pending)} turn(s) into the rolling summary, but memory merge was skipped (invalid model output); original memory.md kept."}
        return {"status": f"Compressed {len(pending)} pending turn(s) into the rolling summary."}
    except Exception as exc:
        return {"status": f"Compression failed: {exc}"}


def advanced_compress(confirm_text: str = "") -> dict:
    """Full dedupe/merge of memory.md (no new facts; forces a tidy-up)."""
    if (confirm_text or "").strip() != "COMPRESS":
        return {"status": "Blocked. Type exactly `COMPRESS` in the confirmation box to proceed."}
    try:
        ensure_files()
        client = get_client()
        merged = merge_memory_with_model(client, [])
        if not merged:
            return {"status": "Memory consolidation skipped: model output invalid; original memory.md kept (backup in memory.md.bak)."}
        return {"status": "Memory file deduplicated and consolidated (backup in memory.md.bak)."}
    except Exception as exc:
        return {"status": f"Advanced compression failed: {exc}"}


def merge_memory_with_model(client: OpenAI, new_facts: list[str]) -> bool:
    """Merge new facts into memory.md: dedupe, prune temporary items, enforce budget.

    Runs under _MEMORY_LOCK (background threads / API threadpool only, never the
    event loop) so concurrent merges cannot lose each other's changes. Writes a
    backup of the previous content to memory.md.bak before overwriting.

    Returns True if memory.md was updated, False if it was skipped (invalid output).
    """
    ensure_files()
    with _MEMORY_LOCK:
        existing = MEMORY_MD_PATH.read_text(encoding="utf-8")

        fact_block = "\n".join(f"- {f}" for f in new_facts if str(f).strip()) if new_facts else "(no new facts)"

        prompt = (
            "You maintain `memory.md`, the assistant's LONG-TERM memory about the user.\n"
            "It must contain EXACTLY two sections under the `# Memory` heading:\n"
            '- "## 长期稳定（Durable）": stable identity, preferences, health, relationships, goals — '
            "these persist long-term and are rarely removed.\n"
            '- "## 近期临时（Temporary）": recent one-off events and temporary states — '
            "these are pruned aggressively on every merge.\n\n"
            "Below are the current file content and new facts.\n"
            "Produce the UPDATED COMPLETE memory.md content (pure markdown, no code block):\n"
            "- Start with the `# Memory` heading.\n"
            "- Merge and deduplicate; near-duplicates collapse into one.\n"
            "- Drop resolved/expired/contradictory items; prune the Temporary section hard.\n"
            f"- Keep the total under ~{MEMORY_SIZE_BUDGET} characters; compress aggressively if over.\n\n"
            "Current memory.md:\n"
            f"{existing}\n\n"
            "New facts:\n"
            f"{fact_block}"
        )

        response = client.chat.completions.create(
            model=get_current_model(),
            messages=[{"role": "user", "content": prompt}],
            extra_body=model_extra_body(get_current_model()),
        )
        msg = response.choices[0].message if response and response.choices else None
        content = (getattr(msg, "content", "") or "").strip() if msg else ""

        # Safety checks before overwriting: must be non-empty and keep the heading.
        if not content:
            return False
        cleaned = _strip_json_fences(content)
        if not cleaned.startswith("# Memory"):
            print("[memory] merge skipped: model output did not start with `# Memory`; original kept.", file=sys.stderr)
            return False

        # Hard budget enforcement: prune the temporary section if still over budget.
        if len(cleaned) > MEMORY_SIZE_BUDGET:
            marker = "## 近期临时"
            idx = cleaned.find(marker)
            if idx > 0:
                cleaned = cleaned[:idx].rstrip() + "\n"
                print("[memory] memory.md exceeded budget; pruned the temporary section.", file=sys.stderr)
            else:
                print(f"[memory] memory.md still over budget ({len(cleaned)} chars) with no temporary section to prune.", file=sys.stderr)

        # Backup the previous content, then overwrite.
        backup_path = MEMORY_MD_PATH.with_suffix(".md.bak")
        backup_path.write_text(existing, encoding="utf-8")
        MEMORY_MD_PATH.write_text(cleaned + "\n", encoding="utf-8")
        return True


def build_context_messages(user_message: dict) -> list[dict]:
    compressed_text = HISTORY_COMPRESSED_MD_PATH.read_text(encoding="utf-8").strip()
    memory_text = MEMORY_MD_PATH.read_text(encoding="utf-8").strip()
    user_text = USER_MD_PATH.read_text(encoding="utf-8").strip()

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant for role-play platform use. Keep responses consistent "
                "with history and memory files. Treat `user.md` as persistent 'who you are' notes "
                "about the user identity/persona, and prioritize alignment with it unless the user "
                "explicitly asks to change it."
            ),
        },
        {
            "role": "system",
            "content": (
                "Use this recent conversation context file — a rolling summary of older "
                "conversation plus recent turns (short/medium-term memory):\n\n"
                f"{compressed_text}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Use this long-term memory file — durable facts about the user (preferences, "
                "health, goals, identity). Treat it as authoritative long-term memory and prefer "
                f"it over the rolling summary when they conflict:\n\n{memory_text}"
            ),
        },
        {
            "role": "system",
            "content": (
                "Use this user profile file as the highest-priority identity memory (who the "
                "user is, role-play persona, preferences, and constraints); it overrides "
                f"conflicts in the other files:\n\n{user_text}"
            ),
        },
        user_message,
    ]
    return messages


# --- FastAPI Models and Routes ---

class ChatRequest(BaseModel):
    message: str
    image_data: str | None = None

class ModelRequest(BaseModel):
    model: str

class SaveProfileRequest(BaseModel):
    content: str

class CompressRequest(BaseModel):
    confirm_text: str = ""

class PopTurnResponse(BaseModel):
    success: bool
    user_text: str = ""
    image_data: str | None = None
    error: str = ""

async def sse_chat_generator(chat_req: ChatRequest):
    try:
        ensure_files()
        client = get_client()

        # Handle base64 image data: write to a temp file only; the single
        # persistence (persist_and_compress_image) happens inside
        # build_user_message below.
        saved_image_path = None
        temp_image_path = None
        if chat_req.image_data and chat_req.image_data.startswith("data:image"):
            import tempfile
            from pathlib import Path
            import uuid
            
            header, encoded = chat_req.image_data.split(",", 1)
            ext = header.split(";")[0].split("/")[1]
            if ext == "jpeg": ext = "jpg"
            
            temp_path = Path(tempfile.gettempdir()) / f"{uuid.uuid4()}.{ext}"
            temp_path.write_bytes(base64.b64decode(encoded))
            saved_image_path = str(temp_path)
            temp_image_path = str(temp_path)

        user_message, memory_user_text, user_plain_text, saved_image_path = build_user_message(
            chat_req.message, saved_image_path
        )
        if temp_image_path:
            Path(temp_image_path).unlink(missing_ok=True)

        context_messages = build_context_messages(user_message)

        extra_body = model_extra_body(get_current_model())

        yield f"data: {json.dumps({'status': 'Thinking...'})}\n\n"

        stream = client.chat.completions.create(
            model=get_current_model(),
            messages=context_messages,
            extra_body=extra_body,
            stream=True,
        )

        answer_parts: list[str] = []
        last_yield_time = 0.0
        update_throttle = 0.06

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            piece = ""
            if delta is not None:
                piece = getattr(delta, "content", "") or ""
            if piece:
                answer_parts.append(piece)
                current_time = time.time()
                if current_time - last_yield_time > update_throttle:
                    yield f"data: {json.dumps({'content': ''.join(answer_parts)})}\n\n"
                    last_yield_time = current_time
                    await asyncio.sleep(0)  # yield control to event loop

        answer = "".join(answer_parts).strip() or "(No text response)"
        yield f"data: {json.dumps({'content': answer})}\n\n"

        # Persist history and pending turn immediately (cheap file I/O) so the
        # UI stays consistent, then close the stream right away. Network-bound
        # work (compression + memory extraction) runs in a background thread so
        # it never blocks the event loop or delays the SSE completion.
        append_turn_history(user_plain_text, answer, saved_image_path)
        with _STATE_LOCK:
            state = load_state()
            state["pending_turns"].append({"user": memory_user_text, "assistant": answer})
            save_state(state)
            rewrite_history_compressed(state)

        yield f"data: {json.dumps({'status': 'Ready.'})}\n\n"
        yield "data: [DONE]\n\n"

        def _post_process() -> None:
            # Rolling compression and eager fact extraction run as network I/O
            # outside _STATE_LOCK; commits (fast file I/O only) happen under the
            # lock so the event loop is never held up.
            try:
                while True:
                    with _STATE_LOCK:
                        state = load_state()
                        pending = list(state.get("pending_turns", []) or [])
                        wm = int(state.get("facts_watermark", 0) or 0)
                        joined = "\n\n".join(
                            f"User: {t.get('user', '')}\nAssistant: {t.get('assistant', '')}"
                            for t in pending
                        )
                        should_compress = (
                            len(pending) >= COMPRESS_EVERY_TURNS
                            or estimate_text_size(joined) >= COMPRESS_SIZE_THRESHOLD
                        )
                        if should_compress:
                            batch = pending
                            rolling_summary = str(state.get("rolling_summary", "") or "")
                            eager_tail = []
                        else:
                            batch = []
                            eager_tail = pending[min(wm, len(pending)):]
                            rolling_summary = ""

                    if batch:
                        try:
                            result = rolling_summarize(client, rolling_summary, batch)
                        except Exception:
                            break

                        with _STATE_LOCK:
                            state = load_state()
                            if state.get("pending_turns", []) == batch:
                                state["rolling_summary"] = result.get("summary", "")
                                state["pending_turns"] = []
                                state["facts_watermark"] = 0
                                save_state(state)
                                rewrite_history_compressed(state)
                            else:
                                # New turns arrived while summarizing; loop again.
                                continue

                        facts = result.get("facts") or []
                        if facts:
                            try:
                                merge_memory_with_model(client, facts)
                            except Exception as exc:
                                print(f"[memory] merge failed: {exc}", file=sys.stderr)
                        continue

                    if len(eager_tail) >= FACTS_EAGER_TURNS:
                        try:
                            eager_facts = extract_facts_only(client, eager_tail)
                        except Exception:
                            break

                        if eager_facts:
                            try:
                                merge_memory_with_model(client, eager_facts)
                            except Exception as exc:
                                print(f"[memory] eager merge failed: {exc}", file=sys.stderr)

                        with _STATE_LOCK:
                            state = load_state()
                            cur_len = len(state.get("pending_turns", []) or [])
                            state["facts_watermark"] = min(cur_len, wm + len(eager_tail))
                            save_state(state)
                        continue

                    break
            except Exception:
                pass

        threading.Thread(target=_post_process, daemon=True).start()

    except Exception as exc:
        if isinstance(exc, AuthenticationError) or "User not found" in str(exc):
            err = "Authentication failed. Set a valid OPENROUTER_API_KEY."
            yield f"data: {json.dumps({'content': f'**Error:** {err}', 'status': 'Error'})}\n\n"
        else:
            yield f"data: {json.dumps({'content': f'**Error:** {exc}', 'status': 'Error'})}\n\n"
        yield "data: [DONE]\n\n"


def build_server() -> FastAPI:
    validate_auth_config()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    server = FastAPI(title="Virtual Life Auth Gateway")

    server.mount("/static", StaticFiles(directory="static"), name="static")
    server.mount("/images", StaticFiles(directory=str(IMAGE_DIR)), name="images")

    @server.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        if auth_dependency(request):
            return RedirectResponse(url="/app", status_code=302)
        return RedirectResponse(url="/login", status_code=302)

    @server.get("/app", response_class=HTMLResponse)
    async def app_page(request: Request):
        if not auth_dependency(request):
            return RedirectResponse(url="/login", status_code=302)
        return HTMLResponse(Path("static/index.html").read_text(encoding="utf-8"))

    @server.middleware("http")
    async def app_auth_middleware(request: Request, call_next):
        if request.url.path.startswith("/api/") or request.url.path in ("/app", "/app/"):
            if not auth_dependency(request):
                # API requests return 401 instead of redirecting so JS can handle it
                if request.url.path.startswith("/api/"):
                    from fastapi.responses import JSONResponse
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
                return RedirectResponse(url="/login", status_code=302)
        return await call_next(request)

    @server.get("/login", response_class=HTMLResponse)
    async def login_page():
        return HTMLResponse(login_page_html())

    @server.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        trust_device: str | None = Form(None),
    ):
        if not auth_guard(username, password):
            return HTMLResponse(login_page_html("Invalid credentials or temporarily locked."), status_code=401)

        token, ttl_seconds = issue_session_token(
            username=username,
            user_agent=request.headers.get("user-agent", ""),
            trust_device=bool(trust_device),
        )
        resp = RedirectResponse(url="/app", status_code=302)
        max_age = ttl_seconds if trust_device else None
        resp.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=max_age,
            httponly=True,
            secure=COOKIE_SECURE,
            samesite="lax",
            path="/",
        )
        return resp

    @server.get("/logout")
    async def logout(request: Request):
        token = request.cookies.get(COOKIE_NAME)
        revoke_session_token(token)
        resp = RedirectResponse(url="/login", status_code=302)
        resp.delete_cookie(COOKIE_NAME, path="/")
        return resp

    # --- API Endpoints ---

    @server.get("/api/model")
    async def api_get_model():
        return {"model": get_current_model(), "choices": MODEL_CHOICES}

    @server.post("/api/model")
    async def api_set_model(req: ModelRequest):
        if not save_model_state(req.model):
            return {"ok": False, "model": get_current_model(), "choices": MODEL_CHOICES, "status": f"Unknown model: {req.model}"}
        return {"ok": True, "model": req.model, "choices": MODEL_CHOICES}

    @server.get("/api/init")
    async def api_init():
        ensure_files()
        state = load_state()
        rewrite_history_compressed(state)
        chat_state = init_chat_ui()
        user_md = load_user_md()
        return {"chat_ui_state": chat_state["chat_ui_state"], "user_md": user_md}

    @server.post("/api/save_profile")
    async def api_save_profile(req: SaveProfileRequest):
        return save_user_md(req.content)

    @server.post("/api/manual_compress")
    async def api_manual_compress():
        return manual_compress()

    @server.post("/api/advanced_compress")
    async def api_advanced_compress(req: CompressRequest):
        return advanced_compress(req.confirm_text)

    @server.post("/api/clear_history")
    async def api_clear_history(req: CompressRequest):
        return clear_all_history(req.confirm_text)

    @server.post("/api/pop_last_turn", response_model=PopTurnResponse)
    async def api_pop_last_turn():
        ensure_files()
        state = load_state()
        if not state.get("pending_turns"):
            return PopTurnResponse(success=False, error="Cannot edit. The last turn is already compressed into long-term memory.")
        
        text = HISTORY_MD_PATH.read_text(encoding="utf-8")
        matches = list(TURN_PATTERN.finditer(text))
        if not matches:
            return PopTurnResponse(success=False, error="Could not parse history.md to pop turn.")
            
        last_match = matches[-1]
        text_before = text[:last_match.start()]
        HISTORY_MD_PATH.write_text(text_before, encoding="utf-8")
        
        state["pending_turns"].pop()
        save_state(state)
        rewrite_history_compressed(state)
        
        user_plain = last_match.group("user").strip()
        image_path = last_match.group("image").strip()
        
        image_data = None
        if image_path:
            img_file = Path(image_path)
            if img_file.exists():
                image_data = file_to_data_url(img_file)
                img_file.unlink(missing_ok=True)
                
        return PopTurnResponse(success=True, user_text=user_plain, image_data=image_data)

    @server.post("/api/chat")
    async def api_chat(req: ChatRequest):
        return StreamingResponse(sse_chat_generator(req), media_type="text/event-stream")

    return server



if __name__ == "__main__":
    app = build_server()
    uvicorn.run(app, host="127.0.0.1", port=7861)
