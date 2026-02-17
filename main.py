import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import gradio as gr
import uvicorn
from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from openai import AuthenticationError, OpenAI
from PIL import Image

load_dotenv()

MODEL_NAME = "google/gemini-3-flash-preview"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
COMPRESS_EVERY_TURNS = 10
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
_AUTH_LOCK = threading.Lock()
_AUTH_STATE: dict[str, dict[str, float | int]] = {}

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

MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "save_memory_fact",
        "description": "Save durable user memory (preferences, profile facts, long-term goals).",
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "One concise durable memory fact about the user.",
                }
            },
            "required": ["fact"],
            "additionalProperties": False,
        },
    },
}

WEB_SEARCH_TRIGGER_TERMS = [
    "latest",
    "today",
    "current",
    "news",
    "recent",
    "right now",
    "this week",
    "this month",
    "this year",
    "price",
    "stock",
    "weather",
    "score",
    "result",
    "release date",
    "update",
]

I18N = {
    "eng": {
        "title": f"# Virtual Life Chat\nModel: `{MODEL_NAME}`",
        "message_label": "Message",
        "message_placeholder": "Type your message and press Enter to send.",
        "image_label": "Image",
        "thinking_label": "Enable thinking",
        "web_search_label": "Enable web search (when needed)",
        "send_btn": "Send",
        "user_profile_accordion": "User Profile (`user.md`)",
        "user_profile_label": "Editable user profile/context",
        "user_profile_placeholder": "Add user preferences, profile, constraints, goals...",
        "save_profile_btn": "Save User Profile",
        "danger_accordion": "Danger Zone",
        "danger_md": (
            "### WARNING: THIS ACTION IS PERMANENT AND CANNOT BE UNDONE.\n"
            "- Deletes **all** chat turns in `history.md`\n"
            "- Deletes compressed context in `history-compressed.md`\n"
            "- Deletes saved profile facts in `memory.md`\n"
            "- Deletes all local images in `chat_images/`\n"
            "- This is an irreversible wipe"
        ),
        "confirm_clear_label": "Type EXACTLY: CLEAR ALL HISTORY",
        "confirm_clear_placeholder": "CLEAR ALL HISTORY",
        "clear_btn": "Clear All History (Irreversible)",
    },
    "chinese_sim": {
        "title": f"# Virtual Life 聊天\n模型: `{MODEL_NAME}`",
        "message_label": "消息",
        "message_placeholder": "输入消息后按 Enter 发送。",
        "image_label": "图片",
        "thinking_label": "开启思考",
        "web_search_label": "开启联网搜索（按需）",
        "send_btn": "发送",
        "user_profile_accordion": "用户资料（`user.md`）",
        "user_profile_label": "可编辑用户资料/上下文",
        "user_profile_placeholder": "填写用户偏好、背景、约束、目标……",
        "save_profile_btn": "保存用户资料",
        "danger_accordion": "危险区域",
        "danger_md": (
            "### 警告：此操作为永久删除，无法恢复。\n"
            "- 删除 `history.md` 中**全部**聊天记录\n"
            "- 删除 `history-compressed.md` 中压缩上下文\n"
            "- 删除 `memory.md` 中保存的用户记忆\n"
            "- 删除 `chat_images/` 中所有本地图片\n"
            "- 此为不可逆清空"
        ),
        "confirm_clear_label": "请精确输入：CLEAR ALL HISTORY",
        "confirm_clear_placeholder": "CLEAR ALL HISTORY",
        "clear_btn": "清空全部历史（不可恢复）",
    },
}

APP_CSS = """
:root {
  --app-max: 1380px;
  --radius: 14px;
}

.gradio-container {
  max-width: var(--app-max) !important;
  margin: 0 auto !important;
  padding: 10px 12px 20px 12px !important;
}

#title_md {
  margin-bottom: 6px !important;
}

#desktop_shell {
  gap: 14px !important;
}

#left_panel,
#right_panel {
  border: 1px solid rgba(120, 120, 120, 0.18) !important;
  border-radius: var(--radius) !important;
  padding: 10px !important;
  background: rgba(245, 245, 245, 0.35) !important;
}

#chatbox {
  min-height: 64vh !important;
  max-height: 72vh !important;
  border-radius: var(--radius) !important;
}

#message_input textarea {
  font-size: 16px !important;
  line-height: 1.35 !important;
}

#status_md {
  margin-top: 6px !important;
}

#send_btn button,
#save_user_btn button,
#clear_btn button {
  min-height: 42px !important;
  border-radius: 10px !important;
}

#controls_row,
#input_row {
  gap: 10px !important;
}

#right_panel .gradio-accordion {
  margin-top: 8px !important;
}

#image_input {
  min-height: 220px !important;
}

@media (max-width: 900px) {
  .gradio-container {
    padding: 8px 8px 14px 8px !important;
  }

  #left_panel,
  #right_panel {
    padding: 8px !important;
  }

  #chatbox {
    min-height: 46vh !important;
    max-height: 58vh !important;
  }
}

@media (max-width: 640px) {
  #chatbox {
    min-height: 42vh !important;
    max-height: 54vh !important;
  }

  #send_btn button {
    width: 100% !important;
  }
}

@media (orientation: landscape) and (max-height: 540px) {
  #chatbox {
    min-height: 36vh !important;
    max-height: 46vh !important;
  }
}
"""


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


def should_use_web_search(user_text: str) -> bool:
    text = (user_text or "").strip().lower()
    if not text:
        return False
    return any(term in text for term in WEB_SEARCH_TRIGGER_TERMS)


def normalize_lang(lang: str | None) -> str:
    return "chinese_sim" if (lang or "").strip().lower() == "chinese_sim" else "eng"


def detect_lang_from_request(request: gr.Request | None) -> str:
    if request is None:
        return "eng"

    try:
        # Prefer Gradio/query locale first.
        qp = dict(getattr(request, "query_params", {}) or {})
        for key in ("__lang", "lang", "language", "locale"):
            raw = str(qp.get(key, "")).strip().lower()
            if raw in ("chinese_sim", "zh-cn", "zh_hans", "zh-hans", "zh"):
                return "chinese_sim"
            if raw:
                return "eng"
    except Exception:
        pass

    try:
        # Fallback: only inspect PRIMARY browser language.
        headers = dict(getattr(request, "headers", {}) or {})
        accept_lang = str(headers.get("accept-language", "")).strip().lower()
        primary = accept_lang.split(",")[0].split(";")[0].strip()
        if primary.startswith("zh"):
            return "chinese_sim"
    except Exception:
        pass

    return "eng"


def ensure_files() -> None:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    if not STATE_PATH.exists():
        save_state({"compressed_memories": [], "pending_turns": []})

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


def load_state() -> dict:
    ensure_files()
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {"compressed_memories": [], "pending_turns": []}
    if not isinstance(state, dict):
        state = {"compressed_memories": [], "pending_turns": []}

    state.setdefault("compressed_memories", [])
    state.setdefault("pending_turns", [])
    if not isinstance(state["compressed_memories"], list):
        state["compressed_memories"] = []
    if not isinstance(state["pending_turns"], list):
        state["pending_turns"] = []

    cleaned_pending = []
    for turn in state["pending_turns"]:
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user", "") or "").strip()
        assistant = str(turn.get("assistant", "") or "").strip()
        if user or assistant:
            cleaned_pending.append({"user": user, "assistant": assistant})
    state["pending_turns"] = cleaned_pending

    cleaned_comp = []
    for item in state["compressed_memories"]:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        summary = str(item.get("summary", "") or "").strip()
        if isinstance(sid, int) and summary:
            cleaned_comp.append({"id": sid, "summary": summary})
    state["compressed_memories"] = cleaned_comp
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


def load_chat_history_for_ui() -> list[dict]:
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
                    data_url = file_to_data_url(img_file)
                    user_content = f"{user_plain}\n\n![uploaded image]({data_url})"
                else:
                    user_content = f"{user_plain}\n\n[Image missing: {image_path}]"
            else:
                user_content = user_plain

            turns.append({"role": "user", "content": user_content})
            turns.append({"role": "assistant", "content": assistant})
        return turns

    legacy_matches = list(LEGACY_TURN_PATTERN.finditer(text))
    for m in legacy_matches:
        user_plain = m.group("user").strip()
        assistant = m.group("assistant").strip()
        turns.append({"role": "user", "content": user_plain})
        turns.append({"role": "assistant", "content": assistant})

    return turns


def init_chat_ui() -> tuple[list[dict], list[dict], str]:
    initial = load_chat_history_for_ui()
    return initial, initial, f"Loaded {len(initial) // 2} turns from history.md"


def apply_language(lang_code: str):
    lang = normalize_lang(lang_code)
    t = I18N[lang]
    return (
        gr.update(value=t["title"]),
        gr.update(label=t["message_label"], placeholder=t["message_placeholder"]),
        gr.update(label=t["image_label"]),
        gr.update(label=t["thinking_label"]),
        gr.update(label=t["web_search_label"]),
        gr.update(value=t["send_btn"]),
        gr.update(label=t["user_profile_accordion"]),
        gr.update(label=t["user_profile_label"], placeholder=t["user_profile_placeholder"]),
        gr.update(value=t["save_profile_btn"]),
        gr.update(label=t["danger_accordion"]),
        gr.update(value=t["danger_md"]),
        gr.update(label=t["confirm_clear_label"], placeholder=t["confirm_clear_placeholder"]),
        gr.update(value=t["clear_btn"]),
    )


def init_language(request: gr.Request):
    detected = detect_lang_from_request(request)
    return apply_language(detected)


def load_user_md() -> str:
    ensure_files()
    return USER_MD_PATH.read_text(encoding="utf-8")


def save_user_md(content: str):
    ensure_files()
    USER_MD_PATH.write_text((content or "").strip() + "\n", encoding="utf-8")
    return f"Saved user profile to `{USER_MD_PATH.as_posix()}`."


def clear_all_history(confirm_text: str):
    required = "CLEAR ALL HISTORY"
    if (confirm_text or "").strip() != required:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            f"Blocked. Type exactly `{required}` to confirm irreversible deletion.",
        )

    empty_state = {"compressed_memories": [], "pending_turns": []}
    save_state(empty_state)

    HISTORY_MD_PATH.write_text("# Chat History\n\n", encoding="utf-8")
    rewrite_history_compressed(empty_state)
    MEMORY_MD_PATH.write_text("# Memory\n\n", encoding="utf-8")

    if IMAGE_DIR.exists():
        for p in IMAGE_DIR.iterdir():
            if p.is_file():
                p.unlink()

    return [], [], "", "History cleared permanently: all conversations, memory, compressed history, and saved images were deleted."


def summarize_turns(client: OpenAI, turns: list[dict]) -> str:
    turns_text = []
    for idx, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user", "") or "")
        assistant = str(turn.get("assistant", "") or "")
        turns_text.append(f"Turn {idx}\nUser: {user}\nAssistant: {assistant}")

    if not turns_text:
        return "No valid turns to summarize."

    prompt = (
        "Compress these chat turns into compact long-term memory.\n"
        "Keep durable user preferences, facts, goals, and open tasks.\n"
        "Do not include chain-of-thought. Use concise bullet points.\n\n"
        + "\n\n".join(turns_text)
    )

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}],
        extra_body={"reasoning": {"enabled": False}},
    )
    msg = response.choices[0].message if response and response.choices else None
    content = (getattr(msg, "content", "") or "").strip() if msg else ""
    return content or "Summary unavailable due to empty model response."


def rewrite_history_compressed(state: dict) -> None:
    lines = ["# History Compressed", "", "This file includes compressed summaries and pending uncompressed turns.", ""]

    lines.append("## Compressed Summaries")
    lines.append("")
    if state["compressed_memories"]:
        for item in state["compressed_memories"]:
            lines.append(f"### Summary {item['id']}")
            lines.append("")
            lines.append(item["summary"])
            lines.append("")
    else:
        lines.append("(none)")
        lines.append("")

    lines.append("## Pending Turns (Uncompressed)")
    lines.append("")
    if state["pending_turns"]:
        for idx, turn in enumerate(state["pending_turns"], start=1):
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


def maybe_compress_history(client: OpenAI, state: dict) -> None:
    while len(state["pending_turns"]) >= COMPRESS_EVERY_TURNS:
        batch = state["pending_turns"][:COMPRESS_EVERY_TURNS]
        try:
            summary_text = summarize_turns(client, batch)
            next_id = len(state["compressed_memories"]) + 1
            state["compressed_memories"].append({"id": next_id, "summary": summary_text})
            state["pending_turns"] = state["pending_turns"][COMPRESS_EVERY_TURNS:]
        except Exception:
            # Do not break chat flow on summarization failures; keep pending turns as-is.
            break


def append_memory_fact(fact: str) -> bool:
    fact = fact.strip()
    if not fact:
        return False

    existing = MEMORY_MD_PATH.read_text(encoding="utf-8")
    marker = f"- {fact}"
    if marker in existing:
        return False

    with MEMORY_MD_PATH.open("a", encoding="utf-8") as f:
        f.write(f"- {fact}\n")
    return True


def extract_memory_with_function_call(client: OpenAI, user_text: str, assistant_text: str) -> int:
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {
                "role": "system",
                "content": (
                    "If there is durable user memory worth saving, call save_memory_fact. "
                    "Examples: stable preferences, long-term goals, profile facts. "
                    "Do not save one-off requests."
                ),
            },
            {
                "role": "user",
                "content": f"User message:\n{user_text}\n\nAssistant response:\n{assistant_text}",
            },
        ],
        tools=[MEMORY_TOOL],
        tool_choice="auto",
        extra_body={"reasoning": {"enabled": False}},
    )

    message = response.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    saved = 0

    for tc in tool_calls:
        func = getattr(tc, "function", None)
        if not func or getattr(func, "name", "") != "save_memory_fact":
            continue

        raw_args = getattr(func, "arguments", "{}") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            continue

        fact = (args.get("fact") or "").strip()
        if append_memory_fact(fact):
            saved += 1

    return saved


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
            "content": f"Use this chat context file:\n\n{compressed_text}",
        },
        {
            "role": "system",
            "content": f"Use this memory file:\n\n{memory_text}",
        },
        {
            "role": "system",
            "content": (
                "Use this user profile file as identity memory (who the user is, role-play persona, "
                f"preferences, and constraints):\n\n{user_text}"
            ),
        },
        user_message,
    ]
    return messages


def chat_once(
    message: str,
    image_path: str | None,
    thinking_enabled: bool,
    web_search_enabled: bool,
    chat_ui_state: list[dict],
):
    try:
        ensure_files()
        client = get_client()
        msg_cleared = False

        user_message, memory_user_text, user_plain_text, saved_image_path = build_user_message(
            message, image_path
        )

        context_messages = build_context_messages(user_message)
        use_web_search = bool(web_search_enabled) and should_use_web_search(user_plain_text)

        extra_body = {"reasoning": {"enabled": bool(thinking_enabled)}}
        if use_web_search:
            extra_body["plugins"] = [{"id": "web"}]
            extra_body["web_search_options"] = {"search_context_size": "medium"}
        chat_ui_state = chat_ui_state or []
        if saved_image_path:
            data_url = file_to_data_url(Path(saved_image_path))
            user_display = f"{user_plain_text}\n\n![uploaded image]({data_url})"
        else:
            user_display = user_plain_text

        chat_ui_state.append({"role": "user", "content": user_display})
        chat_ui_state.append({"role": "assistant", "content": ""})
        yield (
            chat_ui_state,
            chat_ui_state,
            f"Streaming... thinking={bool(thinking_enabled)} web_search={use_web_search}",
            "",
        )
        msg_cleared = True

        stream = client.chat.completions.create(
            model=MODEL_NAME,
            messages=context_messages,
            extra_body=extra_body,
            stream=True,
        )

        answer_parts: list[str] = []
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            piece = ""
            if delta is not None:
                piece = getattr(delta, "content", "") or ""
            if piece:
                answer_parts.append(piece)
                chat_ui_state[-1]["content"] = "".join(answer_parts)
                yield (
                    chat_ui_state,
                    chat_ui_state,
                    f"Streaming... thinking={bool(thinking_enabled)} web_search={use_web_search}",
                    gr.update(),
                )

        answer = "".join(answer_parts).strip() or "(No text response)"
        chat_ui_state[-1]["content"] = answer

        append_turn_history(user_plain_text, answer, saved_image_path)
        state = load_state()
        state["pending_turns"].append({"user": memory_user_text, "assistant": answer})
        maybe_compress_history(client, state)
        save_state(state)
        rewrite_history_compressed(state)
        try:
            extract_memory_with_function_call(client, user_plain_text, answer)
        except Exception:
            # Keep chat flow working even if tool-calling is unavailable.
            pass

        status = (
            f"Done. thinking={bool(thinking_enabled)} "
            f"web_search_enabled={bool(web_search_enabled)} web_search_used={use_web_search}"
        )
        yield chat_ui_state, chat_ui_state, status, gr.update()
    except Exception as exc:
        if isinstance(exc, AuthenticationError) or "User not found" in str(exc):
            err = (
                "OpenRouter authentication failed (401: User not found). "
                "Set a valid OPENROUTER_API_KEY in .env or your shell and restart the app."
            )
            chat_ui_state = chat_ui_state or []
            fallback_user = (message or "").strip() or "(image message)"
            chat_ui_state.append({"role": "user", "content": fallback_user})
            chat_ui_state.append({"role": "assistant", "content": f"Error: {err}"})
            yield chat_ui_state, chat_ui_state, f"Error: {err}", ("" if not msg_cleared else gr.update())
            return

        chat_ui_state = chat_ui_state or []
        fallback_user = (message or "").strip() or "(image message)"
        chat_ui_state.append({"role": "user", "content": fallback_user})
        chat_ui_state.append({"role": "assistant", "content": f"Error: {exc}"})
        yield chat_ui_state, chat_ui_state, f"Error: {exc}", ("" if not msg_cleared else gr.update())
        return


def build_app() -> gr.Blocks:
    ensure_files()
    state = load_state()
    rewrite_history_compressed(state)
    with gr.Blocks(title="Virtual Life Chat (Gradio)", css=APP_CSS) as demo:
        title_md = gr.Markdown(I18N["eng"]["title"], elem_id="title_md")

        chat_ui_state = gr.State([])

        with gr.Row(elem_id="desktop_shell", equal_height=False):
            with gr.Column(scale=8, min_width=680, elem_id="left_panel"):
                chatbot = gr.Chatbot(height=520, elem_id="chatbox")
                with gr.Row(elem_id="input_row"):
                    message = gr.Textbox(
                        label=I18N["eng"]["message_label"],
                        lines=1,
                        placeholder=I18N["eng"]["message_placeholder"],
                        autofocus=True,
                        scale=8,
                        min_width=340,
                        elem_id="message_input",
                    )
                    send = gr.Button(
                        I18N["eng"]["send_btn"],
                        variant="primary",
                        scale=2,
                        min_width=120,
                        elem_id="send_btn",
                    )
                status = gr.Markdown("", elem_id="status_md")

            with gr.Column(scale=4, min_width=300, elem_id="right_panel"):
                image = gr.Image(
                    label=I18N["eng"]["image_label"],
                    type="filepath",
                    elem_id="image_input",
                )
                with gr.Row(elem_id="controls_row"):
                    thinking = gr.Checkbox(
                        label=I18N["eng"]["thinking_label"], value=False, scale=1, min_width=110
                    )
                    web_search = gr.Checkbox(
                        label=I18N["eng"]["web_search_label"], value=True, scale=1, min_width=160
                    )

                with gr.Accordion(I18N["eng"]["user_profile_accordion"], open=False) as user_profile_accordion:
                    user_profile = gr.Textbox(
                        label=I18N["eng"]["user_profile_label"],
                        lines=8,
                        value=load_user_md(),
                        placeholder=I18N["eng"]["user_profile_placeholder"],
                    )
                    save_user_btn = gr.Button(I18N["eng"]["save_profile_btn"], elem_id="save_user_btn")

                with gr.Accordion(I18N["eng"]["danger_accordion"], open=False) as danger_accordion:
                    danger_md = gr.Markdown(I18N["eng"]["danger_md"])
                    confirm_clear = gr.Textbox(
                        label=I18N["eng"]["confirm_clear_label"],
                        lines=1,
                        placeholder=I18N["eng"]["confirm_clear_placeholder"],
                    )
                    clear_btn = gr.Button(I18N["eng"]["clear_btn"], variant="stop", elem_id="clear_btn")

        send.click(
            fn=chat_once,
            inputs=[message, image, thinking, web_search, chat_ui_state],
            outputs=[chatbot, chat_ui_state, status, message],
        )
        message.submit(
            fn=chat_once,
            inputs=[message, image, thinking, web_search, chat_ui_state],
            outputs=[chatbot, chat_ui_state, status, message],
        )
        clear_btn.click(
            fn=clear_all_history,
            inputs=[confirm_clear],
            outputs=[chatbot, chat_ui_state, confirm_clear, status],
        )
        save_user_btn.click(
            fn=save_user_md,
            inputs=[user_profile],
            outputs=[status],
        )
        demo.load(
            fn=init_chat_ui,
            inputs=None,
            outputs=[chatbot, chat_ui_state, status],
        )
        demo.load(
            fn=init_language,
            inputs=None,
            outputs=[
                title_md,
                message,
                image,
                thinking,
                web_search,
                send,
                user_profile_accordion,
                user_profile,
                save_user_btn,
                danger_accordion,
                danger_md,
                confirm_clear,
                clear_btn,
            ],
        )

    return demo


def build_server() -> FastAPI:
    validate_auth_config()
    demo = build_app()
    server = FastAPI(title="Virtual Life Auth Gateway")

    @server.get("/", response_class=HTMLResponse)
    async def root(request: Request):
        if auth_dependency(request):
            return RedirectResponse(url="/app", status_code=302)
        return RedirectResponse(url="/login", status_code=302)

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

    gr.mount_gradio_app(
        app=server,
        blocks=demo,
        path="/app",
        auth_dependency=auth_dependency,
    )
    return server


if __name__ == "__main__":
    app = build_server()
    uvicorn.run(app, host="127.0.0.1", port=7860)
