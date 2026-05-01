import os
import hmac
import hashlib
import base64
import re
import threading
import html
import time
import traceback
import json
import uuid
from datetime import datetime
from urllib.parse import parse_qs, quote, urlencode

import markdown
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response

from ask_claude import ask_claude
from ask_codex import ask_codex

load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
_raw = os.environ.get("LINE_ALLOWED_USER_IDS", os.environ.get("LINE_ALLOWED_USER_ID", ""))
ALLOWED_USER_IDS = {uid.strip() for uid in _raw.split(",") if uid.strip()}
AGENT_BACKEND = os.environ.get("AGENT_BACKEND", "claude").strip().lower()
VAULT_DIR = os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault")
KNOWLEDGE_DIR = os.path.join(VAULT_DIR, "04_Knowledge")
DAILY_DIR = os.path.join(VAULT_DIR, "03_Daily")
OBSIDIAN_VAULT_NAME = os.environ.get("OBSIDIAN_VAULT_NAME", os.path.basename(VAULT_DIR))
OPEN_NOTE_BASE_URL = os.environ.get("OPEN_NOTE_BASE_URL", "").rstrip("/")
OPEN_NOTE_TOKEN = os.environ.get("OPEN_NOTE_TOKEN", "").strip()
OPEN_NOTE_TTL_SECONDS = int(os.environ.get("OPEN_NOTE_TTL_SECONDS", "1800"))
OPEN_NOTE_RESULT_TTL_SECONDS = int(os.environ.get("OPEN_NOTE_RESULT_TTL_SECONDS", "0"))
ANSWER_PAGES_DIR = os.environ.get(
    "ANSWER_PAGES_DIR",
    os.path.join(VAULT_DIR, "02_Projects", "9002-VaultLINEBot", "LineBotResults"),
)
DISCUSSIONS_DIR = os.environ.get(
    "DISCUSSIONS_DIR",
    os.path.join(VAULT_DIR, "02_Projects", "9002-VaultLINEBot", "WebDiscussionSessions"),
)
KNOWLEDGE_ITEMS_PER_PAGE = 5
KNOWLEDGE_NOTES_PER_PAGE = 5
FAST_PREVIEW_LIMIT = 3
REPORT_MODE_TIMEOUT_SECONDS = 5 * 60
USER_MODES: dict[str, dict] = {}
DISCUSSION_FILE_LOCK = threading.RLock()
LAST_REQUEST_BASE_URL = ""
LOG_FILE = os.environ.get("BOT_LOG_FILE", os.path.join(os.path.dirname(__file__), "bot-debug.log"))
APP_ASSETS_DIR = os.environ.get("APP_ASSETS_DIR", os.path.join(os.path.dirname(__file__), "assets"))
MIND_PALACE_ICON_PATH = os.path.join(APP_ASSETS_DIR, "mind-palace-icon.png")

app = FastAPI()


def log_debug(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[LOG ERROR] {e}")


def post_line(url: str, payload: dict):
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"},
        json=payload,
        timeout=20,
    )
    if response.status_code >= 400:
        log_debug(f"[LINE API ERROR] status={response.status_code} body={response.text}")
    return response


def verify_signature(body: bytes, signature: str) -> bool:
    hash_ = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash_).decode()
    return hmac.compare_digest(expected, signature)


def push_message(user_id: str, text: str):
    return post_line(
        "https://api.line.me/v2/bot/message/push",
        {"to": user_id, "messages": with_home_quick_reply([{"type": "text", "text": text[:5000]}])},
    )


def reply_message(reply_token: str, text: str):
    return post_line(
        "https://api.line.me/v2/bot/message/reply",
        {"replyToken": reply_token, "messages": with_home_quick_reply([{"type": "text", "text": text}])},
    )


def reply_messages(reply_token: str, messages: list[dict]):
    return post_line(
        "https://api.line.me/v2/bot/message/reply",
        {"replyToken": reply_token, "messages": with_home_quick_reply(messages)},
    )


def with_home_quick_reply(messages: list[dict]) -> list[dict]:
    if not messages:
        return messages
    prepared = [message.copy() for message in messages]
    prepared[-1].setdefault("quickReply", build_home_quick_reply())
    return prepared


def ask_agent(prompt: str, allow_write: bool = False) -> str:
    if AGENT_BACKEND == "claude":
        return ask_claude(prompt, allow_write=allow_write)
    if AGENT_BACKEND == "codex":
        return ask_codex(prompt, allow_write=allow_write)
    return f"未知 AGENT_BACKEND={AGENT_BACKEND}，請設定為 claude 或 codex。"


WRITE_PREFIXES = ("紀錄：", "紀錄:", "記錄：", "記錄:", "新增紀錄：", "新增紀錄:", "新增記錄：", "新增記錄:")
QUERY_PREFIXES = ("查詢：", "查詢:", "討論：", "討論:")


def parse_input_mode(user_text: str) -> tuple[str, str]:
    for prefix in WRITE_PREFIXES:
        if user_text.startswith(prefix):
            return "report", user_text[len(prefix):].strip()
    for prefix in QUERY_PREFIXES:
        if user_text.startswith(prefix):
            return "query", user_text[len(prefix):].strip()
    return "query", user_text


def build_fast_preview(user_text: str) -> str:
    matches = find_knowledge_index_matches(user_text, FAST_PREVIEW_LIMIT)
    if not matches:
        return "已收到，正在查詢 vault 並整理答案…"

    lines = ["已找到可能相關資料，正在整理正式答案："]
    for entry in matches:
        lines.append(f"• {entry['title']}")
    return "\n".join(lines)


def find_knowledge_index_matches(user_text: str, limit: int) -> list[dict[str, str]]:
    terms = build_search_terms(user_text)
    if not terms:
        return []

    entries = read_knowledge_index_entries()
    scored: list[tuple[int, dict[str, str]]] = []
    life_query = is_life_query(user_text)
    for entry in entries:
        if not life_query and "life" in entry["tags"].lower():
            continue
        haystack = " ".join([entry["page"], entry["title"], entry["summary"], entry["tags"]]).lower()
        score = sum(term_score(term, haystack) for term in terms)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [entry for _, entry in scored[:limit]]


def build_search_terms(user_text: str) -> list[str]:
    lower = user_text.lower()
    terms = re.findall(r"[a-z0-9][a-z0-9_-]*", lower)
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", user_text)
    for chunk in cjk_chunks:
        terms.append(chunk)
        if len(chunk) > 2:
            terms.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))

    seen = set()
    result = []
    for term in terms:
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def term_score(term: str, haystack: str) -> int:
    if term not in haystack:
        return 0
    if len(term) >= 4:
        return 4
    return 2


def is_life_query(user_text: str) -> bool:
    lower = user_text.lower()
    return any(keyword in lower for keyword in ("生活", "life", "個人", "personal"))


def read_knowledge_index_entries() -> list[dict[str, str]]:
    index_path = os.path.join(KNOWLEDGE_DIR, "index.md")
    if not os.path.isfile(index_path):
        return []

    entries = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            match = re.match(r"\|\s*\[\[([^\]]+)\]\]\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|", line)
            if not match:
                continue
            page, summary, tags = [part.strip() for part in match.groups()]
            if page.startswith("-"):
                continue
            title = os.path.basename(page.replace("\\", "/"))
            entries.append(
                {
                    "page": page,
                    "title": title,
                    "summary": strip_markdown_inline(summary),
                    "tags": strip_markdown_inline(tags),
                }
            )
    return entries


def strip_markdown_inline(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return text


def get_project_names() -> list[str]:
    if not os.path.isdir(KNOWLEDGE_DIR):
        return []
    names = [
        entry.name
        for entry in os.scandir(KNOWLEDGE_DIR)
        if entry.is_dir() and not entry.name.startswith(".")
    ]
    return sorted(names)


def get_recent_markdown_notes(folder: str, limit: int | None = None) -> list[str]:
    if not os.path.isdir(folder):
        return []
    notes: list[tuple[float, str]] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if name.lower().endswith(".md"):
                path = os.path.join(root, name)
                notes.append((os.path.getmtime(path), path))
    sorted_paths = [path for _, path in sorted(notes, reverse=True)]
    if limit is None:
        return sorted_paths
    return sorted_paths[:limit]


def build_obsidian_uri(path: str) -> str:
    relative_path = os.path.relpath(path, VAULT_DIR).replace("\\", "/")
    return (
        "obsidian://open?"
        + urlencode(
            {
                "vault": OBSIDIAN_VAULT_NAME,
                "file": relative_path,
            },
            quote_via=quote,
        )
    )


def build_note_open_url(path: str) -> str:
    relative_path = os.path.relpath(path, VAULT_DIR).replace("\\", "/")
    base_url = OPEN_NOTE_BASE_URL or LAST_REQUEST_BASE_URL.rstrip("/")
    if not base_url:
        return "https://example.com/"
    if not OPEN_NOTE_TOKEN:
        return base_url + "/open-note?" + urlencode({"file": relative_path}, quote_via=quote)
    ttl_seconds = get_open_note_ttl_seconds(path)
    exp = int(time.time()) + ttl_seconds if ttl_seconds > 0 else 0
    params = {
        "file": relative_path,
        "exp": str(exp),
        "sig": sign_open_note(relative_path, exp),
    }
    return base_url + "/open-note?" + urlencode(params, quote_via=quote)


def get_open_note_ttl_seconds(path: str) -> int:
    target_path = os.path.abspath(path)
    answer_pages_root = os.path.abspath(ANSWER_PAGES_DIR)
    if target_path == answer_pages_root or target_path.startswith(answer_pages_root + os.sep):
        return OPEN_NOTE_RESULT_TTL_SECONDS
    return OPEN_NOTE_TTL_SECONDS


def make_answer_page(kind: str, prompt: str) -> str:
    os.makedirs(ANSWER_PAGES_DIR, exist_ok=True)
    timestamp = datetime.now()
    suffix = int(time.time() * 1000) % 100000
    filename = f"{timestamp.strftime('%Y%m%d-%H%M%S')}-{kind}-{suffix:05d}.md"
    path = os.path.join(ANSWER_PAGES_DIR, filename)
    write_answer_page(path, kind, prompt, "處理中", "Claude 正在整理答案，請稍後重新整理此頁。")
    return path


def write_answer_page(path: str, kind: str, prompt: str, status: str, body: str):
    title = "LINE Bot 查詢結果" if kind == "query" else "LINE Bot 問題回報結果"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"""---
title: {title}
date: {datetime.now().strftime("%Y-%m-%d")}
tags: [LINEBot, 結果頁]
project: 通用
---

# {title}

狀態：{status}

更新時間：{now}

## 原始輸入

{prompt}

## 結果

{body}
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_discussion_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def discussion_path(discussion_id: str) -> str:
    if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}", discussion_id):
        raise HTTPException(status_code=400, detail="Invalid discussion id")
    return os.path.join(DISCUSSIONS_DIR, f"{discussion_id}.json")


def save_discussion(session: dict):
    os.makedirs(DISCUSSIONS_DIR, exist_ok=True)
    path = discussion_path(session["discussion_id"])
    tmp_path = path + ".tmp"
    with DISCUSSION_FILE_LOCK:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def load_discussion(discussion_id: str) -> dict:
    path = discussion_path(discussion_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Discussion not found")
    with DISCUSSION_FILE_LOCK:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def update_discussion_fields(discussion_id: str, **fields) -> dict:
    path = discussion_path(discussion_id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Discussion not found")
    tmp_path = path + ".tmp"
    with DISCUSSION_FILE_LOCK:
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
        session.update(fields)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return session


def append_discussion_message(session: dict, role: str, content: str):
    session.setdefault("messages", []).append(
        {
            "role": role,
            "content": content,
            "created_at": now_text(),
        }
    )


def build_discussion_context_prompt(title: str, initial_input: str) -> str:
    return f"""Web Discussion Session Prototype：請針對以下主題建立第一輪討論背景包。

請先查詢 vault 相關脈絡，再輸出：
1. 背景摘要
2. 相關來源路徑
3. 初步判斷
4. 後續可追問方向

注意：
- 這是討論模式，不要寫入 vault。
- 不要產生正式筆記。
- 回答請保留可追溯來源。

討論主題：{title}

使用者初始輸入：
{initial_input}
"""


def build_discussion_reply_prompt(session: dict, user_message: str) -> str:
    recent_messages = session.get("messages", [])[-8:]
    recent_text = "\n".join(
        f"{message.get('role', '')}: {message.get('content', '')}" for message in recent_messages
    )
    return f"""Web Discussion Session Prototype：請延續同一個討論 session 回答使用者。

注意：
- 預設不要重新查整個 vault。
- 只根據背景包、session 摘要與最近對話回答。
- 若使用者明確問到新人物、新專案、新期間或要求查資料，才補查 vault。
- 不要寫入 vault。

討論主題：{session.get('title', '')}

第一輪 vault 背景包：
{session.get('vault_context_summary', '')}

目前 session 摘要：
{session.get('session_summary', '')}

最近對話：
{recent_text}

使用者最新追問：
{user_message}
"""


def refresh_session_summary(session: dict):
    messages = session.get("messages", [])[-6:]
    summary_lines = []
    for message in messages:
        role = "使用者" if message.get("role") == "user" else "AI"
        content = message.get("content", "").strip().replace("\n", " ")
        summary_lines.append(f"{role}: {content[:240]}")
    session["session_summary"] = "\n".join(summary_lines)


def process_pending_discussion_messages(discussion_id: str):
    while True:
        session = load_discussion(discussion_id)
        pending_messages = session.get("pending_user_messages", [])
        if not pending_messages:
            session["status"] = "active"
            session["updated_at"] = now_text()
            save_discussion(session)
            return

        user_message = pending_messages.pop(0)
        session["pending_user_messages"] = pending_messages
        session["status"] = "replying"
        session["updated_at"] = now_text()
        save_discussion(session)

        prompt = build_discussion_reply_prompt(session, user_message)
        answer = ask_agent(prompt, allow_write=False)

        session = load_discussion(discussion_id)
        append_discussion_message(session, "assistant", answer or "討論完成，但沒有產生內容。")
        refresh_session_summary(session)
        session["updated_at"] = now_text()
        save_discussion(session)


def run_discussion_context(discussion_id: str):
    try:
        session = update_discussion_fields(discussion_id, status="building_context", updated_at=now_text())
        prompt = build_discussion_context_prompt(session["title"], session["initial_input"])
        answer = ask_agent(prompt, allow_write=False)
        session = load_discussion(discussion_id)
        session["vault_context_summary"] = answer or "背景包建立完成，但沒有產生內容。"
        append_discussion_message(session, "assistant", session["vault_context_summary"])
        refresh_session_summary(session)
        session["status"] = "active" if not session.get("pending_user_messages") else "replying"
        session["updated_at"] = now_text()
        save_discussion(session)
        process_pending_discussion_messages(discussion_id)
    except Exception as e:
        log_debug(f"[DISCUSSION CONTEXT ERROR] id={discussion_id} error={e}\n{traceback.format_exc()}")
        session = load_discussion(discussion_id)
        session["status"] = "error"
        session["error"] = str(e)
        session["updated_at"] = now_text()
        save_discussion(session)


def run_discussion_reply(discussion_id: str, user_message: str):
    try:
        session = load_discussion(discussion_id)
        prompt = build_discussion_reply_prompt(session, user_message)
        answer = ask_agent(prompt, allow_write=False)
        session = load_discussion(discussion_id)
        append_discussion_message(session, "assistant", answer or "討論完成，但沒有產生內容。")
        refresh_session_summary(session)
        session["status"] = "active" if not session.get("pending_user_messages") else "replying"
        session["updated_at"] = now_text()
        save_discussion(session)
        process_pending_discussion_messages(discussion_id)
    except Exception as e:
        log_debug(f"[DISCUSSION REPLY ERROR] id={discussion_id} error={e}\n{traceback.format_exc()}")
        session = load_discussion(discussion_id)
        append_discussion_message(session, "assistant", f"回覆失敗：{e}")
        session["status"] = "error"
        session["error"] = str(e)
        session["updated_at"] = now_text()
        save_discussion(session)


def build_answer_page_message(path: str, prompt: str, kind: str) -> dict:
    title = "Vault 查詢" if kind == "query" else "Vault 回報"
    created_at = datetime.now().strftime("%Y.%m.%d %H:%M 建立")
    prompt_label = "查詢內容" if kind == "query" else "回報內容"
    return {
        "type": "flex",
        "altText": title,
        "contents": {
            "type": "bubble",
            "size": "mega",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "contents": [
                    build_card_header(title),
                    {
                        "type": "text",
                        "text": "結果頁已建立",
                        "size": "xl",
                        "weight": "bold",
                        "color": "#111827",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": created_at,
                        "size": "xs",
                        "color": "#6B7280",
                        "wrap": True,
                    },
                    {
                        "type": "separator",
                        "margin": "lg",
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": prompt_label,
                                "size": "sm",
                                "color": "#6B7280",
                                "flex": 1,
                            },
                            {
                                "type": "text",
                                "text": shorten_label(prompt, 72),
                                "size": "sm",
                                "color": "#111827",
                                "align": "end",
                                "wrap": True,
                                "flex": 2,
                            },
                        ],
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingTop": "0px",
                "paddingStart": "20px",
                "paddingEnd": "20px",
                "paddingBottom": "20px",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "color": "#06C755",
                        "action": {
                            "type": "uri",
                            "label": "開啟",
                            "uri": build_note_open_url(path),
                        },
                    }
                ],
            },
        },
        "quickReply": build_home_quick_reply(),
    }


def build_card_header(label: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "sm",
                "weight": "bold",
                "color": "#06C755",
                "flex": 1,
            },
            {
                "type": "image",
                "url": build_app_asset_url("mind-palace-icon.png"),
                "size": "32px",
                "aspectMode": "fit",
                "flex": 0,
            },
        ],
    }


def build_app_asset_url(filename: str) -> str:
    base_url = OPEN_NOTE_BASE_URL or LAST_REQUEST_BASE_URL
    if not base_url:
        base_url = "http://localhost:8000"
    url = base_url.rstrip("/") + "/" + filename.lstrip("/")
    asset_path = os.path.join(APP_ASSETS_DIR, filename.lstrip("/"))
    if os.path.isfile(asset_path):
        return url + "?" + urlencode({"v": str(int(os.path.getmtime(asset_path)))})
    return url


def build_mind_palace_icon_png() -> bytes:
    import struct
    import zlib

    size = 64
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            dx = x - 31.5
            dy = y - 31.5
            distance = (dx * dx + dy * dy) ** 0.5
            if distance > 30:
                row.extend((0, 0, 0, 0))
                continue
            glow = max(0, 1 - distance / 30)
            r = int(42 + 92 * glow + 28 * (x / size))
            g = int(55 + 88 * glow + 54 * (1 - y / size))
            b = int(150 + 92 * glow)
            alpha = 255
            if 18 < distance < 24 and x > y - 6:
                r, g, b = 40, 221, 184
            if distance < 9:
                r, g, b = 245, 250, 255
            row.extend((r, g, b, alpha))
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


@app.get("/mind-palace-icon.png")
def mind_palace_icon():
    if os.path.isfile(MIND_PALACE_ICON_PATH):
        with open(MIND_PALACE_ICON_PATH, "rb") as f:
            return Response(content=f.read(), media_type="image/png")
    return Response(content=build_mind_palace_icon_png(), media_type="image/png")


def sign_open_note(file_path: str, exp: int) -> str:
    if not OPEN_NOTE_TOKEN:
        raise HTTPException(status_code=503, detail="OPEN_NOTE_TOKEN is not configured")
    message = f"{file_path}\n{exp}".encode("utf-8")
    return hmac.new(OPEN_NOTE_TOKEN.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_open_note_signature(file_path: str, exp: int, sig: str):
    if not OPEN_NOTE_TOKEN:
        raise HTTPException(status_code=503, detail="OPEN_NOTE_TOKEN is not configured")
    if exp > 0 and exp < int(time.time()):
        raise HTTPException(status_code=403, detail="Open-note link expired")
    expected = sign_open_note(file_path, exp)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=403, detail="Invalid open-note signature")


def get_today_daily_report_path() -> str:
    today_name = datetime.now().strftime("%Y%m%d-daily-report.md")
    today_path = os.path.join(DAILY_DIR, today_name)
    return today_path if os.path.isfile(today_path) else ""


def get_latest_daily_report_path() -> str:
    if not os.path.isdir(DAILY_DIR):
        return ""
    notes = [
        os.path.join(DAILY_DIR, name)
        for name in os.listdir(DAILY_DIR)
        if re.fullmatch(r"\d{8}-daily-report\.md", name.lower())
    ]
    if not notes:
        return ""
    return max(notes, key=lambda path: os.path.basename(path))


def shorten_label(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def strip_frontmatter(content: str) -> str:
    if content.startswith("---\n"):
        end = content.find("\n---", 4)
        if end != -1:
            return content[end + 4 :].lstrip()
    return content


def auto_fence_json_blocks(content: str) -> str:
    lines = content.splitlines()
    rendered: list[str] = []
    in_fence = False
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            rendered.append(line)
            i += 1
            continue

        if not in_fence and stripped in {"{", "["}:
            candidate: list[str] = []
            j = i
            while j < len(lines):
                candidate.append(lines[j])
                try:
                    json.loads("\n".join(candidate))
                    rendered.append("```json")
                    rendered.extend(candidate)
                    rendered.append("```")
                    i = j + 1
                    break
                except json.JSONDecodeError:
                    j += 1
            else:
                rendered.append(line)
                i += 1
            continue

        rendered.append(line)
        i += 1

    return "\n".join(rendered)


def render_markdown(content: str) -> str:
    prepared_content = auto_fence_json_blocks(strip_frontmatter(content))
    return markdown.markdown(
        prepared_content,
        extensions=[
            "extra",
            "fenced_code",
            "sane_lists",
            "nl2br",
            "toc",
        ],
        output_format="html5",
    )


def get_request_base_url(request: Request) -> str:
    forwarded_host = request.headers.get("x-forwarded-host")
    forwarded_proto = request.headers.get("x-forwarded-proto", "https")
    if forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def parse_postback(data: str) -> tuple[str, dict[str, str]]:
    parsed = parse_qs(data, keep_blank_values=True)
    action = parsed.get("action", [""])[0]
    params = {key: values[0] for key, values in parsed.items() if values}
    return action, params


def build_home_text() -> str:
    return "請用下方 Rich Menu 操作，或輸入「選單」顯示功能按鈕。"


def build_home_quick_reply() -> dict:
    return {
        "items": [
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "查詢專案",
                    "text": "查詢專案",
                },
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "回報問題",
                    "text": "回報問題",
                },
            },
            {
                "type": "action",
                "action": {
                    "type": "message",
                    "label": "Daily Report",
                    "text": "今日 Daily Report",
                },
            },
        ]
    }


def build_home_menu_message() -> dict:
    return {
        "type": "flex",
        "altText": "功能選單",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "功能選單",
                        "weight": "bold",
                        "size": "xl",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": "電腦版 LINE 可用下方按鈕操作；手機版也可以繼續使用 Rich Menu。",
                        "size": "sm",
                        "color": "#666666",
                        "wrap": True,
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "action": {
                            "type": "message",
                            "label": "查詢專案",
                            "text": "查詢專案",
                        },
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {
                            "type": "message",
                            "label": "回報問題",
                            "text": "回報問題",
                        },
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {
                            "type": "message",
                            "label": "Daily Report",
                            "text": "今日 Daily Report",
                        },
                    },
                ],
            },
        },
        "quickReply": build_home_quick_reply(),
    }


def set_user_mode(user_id: str, mode: str):
    USER_MODES[user_id] = {"mode": mode, "started_at": time.time()}


def clear_user_mode(user_id: str):
    USER_MODES.pop(user_id, None)


def get_user_mode(user_id: str) -> str:
    state = USER_MODES.get(user_id)
    if not state:
        return ""
    if state.get("mode") == "report":
        started_at = float(state.get("started_at", 0))
        if time.time() - started_at > REPORT_MODE_TIMEOUT_SECONDS:
            clear_user_mode(user_id)
            return ""
    return str(state.get("mode", ""))


def build_project_list_flex(page: int = 0) -> dict:
    projects = get_project_names()
    total = len(projects)
    total_pages = max((total - 1) // KNOWLEDGE_ITEMS_PER_PAGE + 1, 1)

    if not projects:
        return {
            "type": "flex",
            "altText": "Knowledge 清單",
            "contents": {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "paddingAll": "20px",
                    "contents": [
                        build_card_header("Knowledge 摘要"),
                        {
                            "type": "text",
                            "text": "目前找不到 Knowledge 資料夾。",
                            "size": "md",
                            "margin": "lg",
                            "wrap": True,
                        },
                    ],
                },
            },
            "quickReply": build_home_quick_reply(),
        }

    bubbles = []
    for page_index in range(total_pages):
        start = page_index * KNOWLEDGE_ITEMS_PER_PAGE
        end = start + KNOWLEDGE_ITEMS_PER_PAGE
        page_items = projects[start:end]
        bubbles.append(build_project_page_bubble(page_items, page_index, total_pages))

    return {
        "type": "flex",
        "altText": "Knowledge 清單",
        "contents": {
            "type": "carousel",
            "contents": bubbles,
        },
        "quickReply": build_home_quick_reply(),
    }


def build_project_page_bubble(page_items: list[str], page: int, total_pages: int) -> dict:
    contents: list[dict] = [
        build_card_header("Knowledge 摘要"),
        {
            "type": "text",
            "text": f"第 {page + 1} 頁，共 {total_pages} 頁",
            "size": "sm",
            "color": "#666666",
            "margin": "md",
        },
    ]

    for name in page_items:
        contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": name,
                        "weight": "bold",
                        "wrap": True,
                    },
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "action": {
                            "type": "postback",
                            "label": "查看摘要",
                            "data": "action=project_summary&"
                            + urlencode({"project": name}, quote_via=quote),
                            "displayText": f"查看知識：{name}",
                        },
                    },
                ],
            }
        )

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "20px",
            "contents": contents,
        },
    }


def build_project_summary(project_name: str) -> dict:
    knowledge_folder = os.path.join(KNOWLEDGE_DIR, project_name)
    if not os.path.isdir(knowledge_folder):
        return {
            "type": "text",
            "text": f"找不到 Knowledge：{project_name}",
            "quickReply": build_home_quick_reply(),
        }

    recent_notes = get_recent_markdown_notes(knowledge_folder)
    total_pages = max((len(recent_notes) - 1) // KNOWLEDGE_NOTES_PER_PAGE + 1, 1)
    if len(recent_notes) > KNOWLEDGE_NOTES_PER_PAGE:
        bubbles = []
        for page_index in range(total_pages):
            start = page_index * KNOWLEDGE_NOTES_PER_PAGE
            end = start + KNOWLEDGE_NOTES_PER_PAGE
            bubbles.append(
                build_project_summary_bubble(
                    project_name,
                    recent_notes[start:end],
                    page_index,
                    total_pages,
                )
            )
        return {
            "type": "flex",
            "altText": f"{project_name} Knowledge 筆記",
            "contents": {
                "type": "carousel",
                "contents": bubbles,
            },
            "quickReply": build_home_quick_reply(),
        }

    return {
        "type": "flex",
        "altText": f"{project_name} Knowledge 筆記",
        "contents": build_project_summary_bubble(project_name, recent_notes, 0, total_pages),
        "quickReply": build_home_quick_reply(),
    }


def build_project_summary_bubble(
    project_name: str,
    recent_notes: list[str],
    page: int = 0,
    total_pages: int = 1,
) -> dict:
    contents: list[dict] = [
        {
            "type": "text",
            "text": project_name,
            "weight": "bold",
            "size": "lg",
            "wrap": True,
        },
        {
            "type": "text",
            "text": "最近 Knowledge 筆記",
            "size": "sm",
            "color": "#666666",
            "margin": "sm",
        },
    ]
    if total_pages > 1:
        contents.append(
            {
                "type": "text",
                "text": f"第 {page + 1} / {total_pages} 頁",
                "size": "xs",
                "color": "#999999",
                "margin": "xs",
            }
        )
    if not recent_notes:
        contents.append(
            {
                "type": "text",
                "text": "目前沒有 markdown 筆記",
                "size": "md",
                "margin": "lg",
                "wrap": True,
            }
        )
    else:
        for path in recent_notes:
            title = os.path.splitext(os.path.basename(path))[0]
            contents.append(
                {
                    "type": "button",
                    "style": "secondary",
                    "height": "sm",
                    "margin": "md",
                    "action": {
                        "type": "uri",
                        "label": shorten_label(title),
                        "uri": build_note_open_url(path),
                    },
                }
            )

    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents,
        },
    }


def build_daily_report_message() -> dict:
    today_path = get_today_daily_report_path()
    latest_path = get_latest_daily_report_path()
    if not today_path and not latest_path:
        return {
            "type": "text",
            "text": "目前找不到 Daily Report。",
            "quickReply": build_home_quick_reply(),
        }

    target_path = today_path or latest_path
    title = os.path.splitext(os.path.basename(target_path))[0]
    status_text = "今日報告已就緒" if today_path else "顯示最近一份報告"
    detail_label = "來源" if today_path else "原因"
    detail_text = "今日 Daily Report" if today_path else "今日尚未產生"
    return {
        "type": "flex",
        "altText": "Daily Report",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "contents": [
                    build_card_header("Daily Report"),
                    {
                        "type": "text",
                        "text": status_text,
                        "size": "xl",
                        "weight": "bold",
                        "color": "#111827",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": title,
                        "size": "xs",
                        "color": "#6B7280",
                        "wrap": True,
                    },
                    {
                        "type": "separator",
                        "margin": "lg",
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "margin": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": detail_label,
                                "size": "sm",
                                "color": "#6B7280",
                                "flex": 1,
                            },
                            {
                                "type": "text",
                                "text": detail_text,
                                "size": "sm",
                                "color": "#111827",
                                "align": "end",
                                "wrap": True,
                                "flex": 2,
                            },
                        ],
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "paddingTop": "0px",
                "paddingStart": "20px",
                "paddingEnd": "20px",
                "paddingBottom": "20px",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "height": "sm",
                        "color": "#06C755",
                        "action": {
                            "type": "uri",
                            "label": "開啟",
                            "uri": build_note_open_url(target_path),
                        },
                    }
                ],
            },
        },
        "quickReply": build_home_quick_reply(),
    }


@app.get("/discussions", response_class=HTMLResponse)
def discussions_home():
    return HTMLResponse(
        """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Web Discussion Session</title>
  <style>
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #172033;
      background: #f6f8fb;
    }
    main {
      max-width: 860px;
      margin: 0 auto;
      padding: 28px 18px 48px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 28px;
      line-height: 1.25;
      letter-spacing: 0;
    }
    p {
      margin: 0 0 18px;
      color: #526070;
      line-height: 1.65;
    }
    label {
      display: block;
      margin: 18px 0 8px;
      font-weight: 700;
    }
    input, textarea {
      box-sizing: border-box;
      width: 100%;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 12px;
      font: inherit;
      background: #ffffff;
    }
    textarea {
      min-height: 180px;
      resize: vertical;
    }
    button {
      margin-top: 18px;
      border: 0;
      border-radius: 8px;
      padding: 12px 16px;
      font: inherit;
      font-weight: 700;
      color: #ffffff;
      background: #1264a3;
      cursor: pointer;
    }
    button:disabled {
      cursor: wait;
      background: #94a3b8;
    }
    .status {
      min-height: 24px;
      margin-top: 14px;
      color: #475569;
    }
  </style>
</head>
<body>
  <main>
    <h1>Web Discussion Session</h1>
    <p>獨立 prototype。先不接 LINE，先驗證多輪討論、背景包與 claude -p 的體感。</p>
    <form id="discussion-form">
      <label for="title">討論主題</label>
      <input id="title" name="title" autocomplete="off" required placeholder="例如：Pilot 把第一線窗口交給 Ray 的風險">
      <label for="initial_input">初始內容</label>
      <textarea id="initial_input" name="initial_input" required placeholder="貼上你想討論的事件、背景或問題"></textarea>
      <button id="submit-button" type="submit">建立討論</button>
      <div id="status" class="status"></div>
    </form>
  </main>
  <script>
    const form = document.getElementById("discussion-form");
    const statusEl = document.getElementById("status");
    const button = document.getElementById("submit-button");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      button.disabled = true;
      statusEl.textContent = "建立中...";
      const payload = {
        title: document.getElementById("title").value,
        initial_input: document.getElementById("initial_input").value
      };
      const response = await fetch("/api/discussions", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        statusEl.textContent = "建立失敗";
        button.disabled = false;
        return;
      }
      const data = await response.json();
      window.location.href = "/discussions/" + data.discussion_id;
    });
  </script>
</body>
</html>"""
    )


@app.post("/api/discussions")
async def create_discussion(request: Request):
    payload = await request.json()
    title = str(payload.get("title", "")).strip()
    initial_input = str(payload.get("initial_input", "")).strip()
    if not title or not initial_input:
        raise HTTPException(status_code=400, detail="title and initial_input are required")
    discussion_id = make_discussion_id()
    session = {
        "discussion_id": discussion_id,
        "created_at": now_text(),
        "updated_at": now_text(),
        "source": "web-prototype",
        "user_id": "local",
        "title": title,
        "initial_input": initial_input,
        "vault_context_summary": "",
        "vault_sources": [],
        "session_summary": "",
        "pending_user_messages": [],
        "messages": [
            {
                "role": "user",
                "content": initial_input,
                "created_at": now_text(),
            }
        ],
        "status": "queued",
    }
    save_discussion(session)
    threading.Thread(target=run_discussion_context, args=(discussion_id,), daemon=True).start()
    return {"discussion_id": discussion_id, "status": session["status"]}


@app.get("/api/discussions/{discussion_id}")
def get_discussion(discussion_id: str):
    return load_discussion(discussion_id)


@app.post("/api/discussions/{discussion_id}/messages")
async def post_discussion_message(discussion_id: str, request: Request):
    payload = await request.json()
    content = str(payload.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    session = load_discussion(discussion_id)
    if session.get("status") in {"building_context", "replying", "queued"}:
        append_discussion_message(session, "user", content)
        session.setdefault("pending_user_messages", []).append(content)
        session["updated_at"] = now_text()
        save_discussion(session)
        return {
            "discussion_id": discussion_id,
            "status": session.get("status"),
            "queued": True,
        }
    append_discussion_message(session, "user", content)
    session["status"] = "replying"
    session["updated_at"] = now_text()
    save_discussion(session)
    threading.Thread(target=run_discussion_reply, args=(discussion_id, content), daemon=True).start()
    return {"discussion_id": discussion_id, "status": "replying", "queued": False}


@app.get("/discussions/{discussion_id}", response_class=HTMLResponse)
def discussion_page(discussion_id: str):
    session = load_discussion(discussion_id)
    title = html.escape(session.get("title", "Web Discussion Session"))
    escaped_id = html.escape(discussion_id)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #172033;
      background: #f6f8fb;
    }}
    main {{
      max-width: 980px;
      margin: 0 auto;
      padding: 22px 14px 44px;
    }}
    header {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 24px;
      line-height: 1.3;
      letter-spacing: 0;
    }}
    .meta {{
      margin-top: 6px;
      color: #64748b;
      font-size: 14px;
    }}
    .status {{
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 6px 10px;
      color: #0f3a5b;
      background: #d9ecff;
      font-size: 13px;
      font-weight: 700;
    }}
    .layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 300px;
      gap: 16px;
    }}
    .panel {{
      border: 1px solid #d7dee8;
      border-radius: 8px;
      background: #ffffff;
    }}
    .messages {{
      min-height: 420px;
      padding: 14px;
    }}
    .message {{
      margin-bottom: 14px;
      padding: 12px;
      border-radius: 8px;
      line-height: 1.65;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .user {{
      background: #e9f5ee;
    }}
    .assistant {{
      background: #f8fafc;
      border: 1px solid #e2e8f0;
    }}
    .role {{
      display: block;
      margin-bottom: 4px;
      color: #475569;
      font-size: 13px;
      font-weight: 700;
    }}
    form {{
      display: grid;
      gap: 10px;
      padding: 14px;
      border-top: 1px solid #d7dee8;
      background: #ffffff;
    }}
    textarea {{
      box-sizing: border-box;
      width: 100%;
      min-height: 96px;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      padding: 12px;
      font: inherit;
      resize: vertical;
    }}
    button {{
      justify-self: start;
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      font: inherit;
      font-weight: 700;
      color: #ffffff;
      background: #1264a3;
      cursor: pointer;
    }}
    button:disabled {{
      cursor: wait;
      background: #94a3b8;
    }}
    aside {{
      padding: 14px;
      line-height: 1.6;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 16px;
      letter-spacing: 0;
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 0;
      color: #334155;
      font-family: inherit;
      font-size: 14px;
    }}
    @media (max-width: 760px) {{
      header {{
        display: block;
      }}
      .status {{
        display: inline-block;
        margin-top: 10px;
      }}
      .layout {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{title}</h1>
        <div class="meta">discussion_id: {escaped_id}</div>
      </div>
      <div id="status" class="status">loading</div>
    </header>
    <div class="layout">
      <section class="panel">
        <div id="messages" class="messages"></div>
        <form id="message-form">
          <textarea id="content" required placeholder="輸入下一輪追問"></textarea>
          <button id="send-button" type="submit">送出</button>
        </form>
      </section>
      <aside class="panel">
        <h2>Session 摘要</h2>
        <pre id="summary"></pre>
      </aside>
    </div>
  </main>
  <script>
    const discussionId = "{escaped_id}";
    const messagesEl = document.getElementById("messages");
    const summaryEl = document.getElementById("summary");
    const statusEl = document.getElementById("status");
    const form = document.getElementById("message-form");
    const contentEl = document.getElementById("content");
    const button = document.getElementById("send-button");

    function escapeHtml(value) {{
      return value.replace(/[&<>"']/g, (char) => ({{
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }}[char]));
    }}

    function render(session) {{
      statusEl.textContent = session.status;
      button.disabled = false;
      messagesEl.innerHTML = session.messages.map((message) => {{
        const role = message.role === "user" ? "使用者" : "AI";
        const cls = message.role === "user" ? "user" : "assistant";
        return `<div class="message ${{cls}}"><span class="role">${{role}} · ${{escapeHtml(message.created_at || "")}}</span>${{escapeHtml(message.content || "")}}</div>`;
      }}).join("");
      summaryEl.textContent = session.session_summary || "背景包建立中...";
    }}

    async function loadSession() {{
      const response = await fetch("/api/discussions/" + discussionId);
      if (!response.ok) return;
      const session = await response.json();
      render(session);
    }}

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const content = contentEl.value.trim();
      if (!content) return;
      button.disabled = true;
      const response = await fetch("/api/discussions/" + discussionId + "/messages", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{content}})
      }});
      if (response.ok) {{
        contentEl.value = "";
      }}
      button.disabled = false;
      await loadSession();
    }});

    loadSession();
    setInterval(loadSession, 2500);
  </script>
</body>
</html>"""
    )


@app.get("/open-note")
def open_note(file: str, exp: int = 0, sig: str = ""):
    verify_open_note_signature(file, exp, sig)
    normalized = file.replace("\\", "/").lstrip("/")
    target_path = os.path.abspath(os.path.join(VAULT_DIR, normalized))
    vault_root = os.path.abspath(VAULT_DIR)
    if not target_path.startswith(vault_root + os.sep) or not target_path.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not os.path.isfile(target_path):
        raise HTTPException(status_code=404, detail="Note not found")
    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()
    title = os.path.splitext(os.path.basename(target_path))[0]
    escaped_title = html.escape(title)
    escaped_file = html.escape(normalized)
    rendered_content = render_markdown(content)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    body {{
      margin: 0;
      padding: 20px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.65;
      color: #1f2933;
      background: #f7f7f4;
    }}
    main {{
      max-width: 860px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #deded8;
      border-radius: 8px;
      padding: 18px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: 22px;
      line-height: 1.3;
    }}
    .path {{
      margin: 0 0 18px;
      color: #667085;
      font-size: 13px;
      word-break: break-all;
    }}
    .content {{
      font-size: 16px;
    }}
    .content h1,
    .content h2,
    .content h3 {{
      line-height: 1.3;
      margin: 24px 0 10px;
      color: #111827;
    }}
    .content h1 {{
      font-size: 24px;
      border-bottom: 1px solid #e5e7eb;
      padding-bottom: 8px;
    }}
    .content h2 {{
      font-size: 20px;
    }}
    .content h3 {{
      font-size: 17px;
    }}
    .content p {{
      margin: 10px 0;
    }}
    .content ul,
    .content ol {{
      padding-left: 22px;
    }}
    .content li {{
      margin: 4px 0;
    }}
    .content table {{
      width: 100%;
      border-collapse: collapse;
      margin: 14px 0;
      display: block;
      overflow-x: auto;
    }}
    .content th,
    .content td {{
      border: 1px solid #d0d5dd;
      padding: 8px 10px;
      vertical-align: top;
    }}
    .content th {{
      background: #f3f4f6;
      font-weight: 700;
    }}
    .content code {{
      background: #f3f4f6;
      border-radius: 4px;
      padding: 2px 4px;
      font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
      font-size: 0.92em;
    }}
    .content pre {{
      position: relative;
      background: #111827;
      color: #f9fafb;
      border-radius: 8px;
      padding: 12px;
      overflow-x: auto;
    }}
    .code-block {{
      position: relative;
      margin: 14px 0;
    }}
    .code-block pre {{
      margin: 0;
      padding-top: 42px;
    }}
    .copy-code {{
      position: absolute;
      top: 8px;
      right: 8px;
      z-index: 1;
      border: 1px solid rgba(255, 255, 255, 0.18);
      border-radius: 6px;
      padding: 5px 9px;
      background: rgba(17, 24, 39, 0.92);
      color: #f9fafb;
      font-size: 12px;
      line-height: 1;
      cursor: pointer;
    }}
    .copy-code:hover {{
      background: #374151;
    }}
    .copy-code:focus-visible {{
      outline: 2px solid #93c5fd;
      outline-offset: 2px;
    }}
    .content pre code {{
      background: transparent;
      color: inherit;
      padding: 0;
    }}
    .content blockquote {{
      margin: 12px 0;
      padding: 2px 14px;
      border-left: 4px solid #98a2b3;
      color: #475467;
      background: #f9fafb;
    }}
    .content a {{
      color: #2563eb;
      text-decoration: none;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escaped_title}</h1>
    <p class="path">{escaped_file}</p>
    <article class="content">{rendered_content}</article>
  </main>
  <script>
    (() => {{
      const copyText = async (text) => {{
        if (navigator.clipboard && window.isSecureContext) {{
          await navigator.clipboard.writeText(text);
          return;
        }}
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.top = "-1000px";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }};

      document.querySelectorAll(".content pre").forEach((pre) => {{
        if (pre.parentElement && pre.parentElement.classList.contains("code-block")) {{
          return;
        }}
        const wrapper = document.createElement("div");
        wrapper.className = "code-block";
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(pre);

        const button = document.createElement("button");
        button.type = "button";
        button.className = "copy-code";
        button.textContent = "複製";
        button.setAttribute("aria-label", "複製程式碼");
        wrapper.appendChild(button);

        button.addEventListener("click", async () => {{
          const code = pre.querySelector("code");
          const text = code ? code.innerText : pre.innerText;
          const original = button.textContent;
          try {{
            await copyText(text.replace(/\\n$/, ""));
            button.textContent = "已複製";
          }} catch (error) {{
            button.textContent = "複製失敗";
          }}
          window.setTimeout(() => {{
            button.textContent = original;
          }}, 1400);
        }});
      }});
    }})();
  </script>
</body>
</html>"""
    )


def start_report_mode(reply_token: str, user_id: str):
    set_user_mode(user_id, "report")
    reply_messages(
        reply_token,
        [
            {
                "type": "flex",
                "altText": "Vault 回報",
                "contents": {
                    "type": "bubble",
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "md",
                        "paddingAll": "20px",
                        "contents": [
                            build_card_header("Vault 回報"),
                            {
                                "type": "text",
                                "text": "回報模式已開啟",
                                "weight": "bold",
                                "size": "xl",
                                "color": "#111827",
                                "wrap": True,
                            },
                            {
                                "type": "text",
                                "text": "下一則訊息會記錄到 vault",
                                "size": "xs",
                                "color": "#6B7280",
                                "wrap": True,
                            },
                            {
                                "type": "separator",
                                "margin": "lg",
                            },
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "margin": "md",
                                "contents": [
                                    {
                                        "type": "text",
                                        "text": "有效時間",
                                        "size": "sm",
                                        "color": "#6B7280",
                                        "flex": 1,
                                    },
                                    {
                                        "type": "text",
                                        "text": "5 分鐘",
                                        "size": "sm",
                                        "color": "#111827",
                                        "align": "end",
                                        "flex": 2,
                                    },
                                ],
                            },
                        ],
                    },
                    "footer": {
                        "type": "box",
                        "layout": "vertical",
                        "paddingTop": "0px",
                        "paddingStart": "20px",
                        "paddingEnd": "20px",
                        "paddingBottom": "20px",
                        "contents": [
                            {
                                "type": "button",
                                "style": "secondary",
                                "height": "sm",
                                "action": {
                                    "type": "postback",
                                    "label": "取消回報",
                                    "data": "action=cancel_report",
                                    "displayText": "取消回報",
                                },
                            },
                        ],
                    },
                },
            },
        ],
    )


def run_agent_and_push(user_id: str, prompt: str):
    start = time.time()
    log_debug(f"[AGENT START] user={user_id} backend={AGENT_BACKEND} prompt={prompt[:80]!r}")
    try:
        answer = ask_agent(prompt, allow_write=False)
        log_debug(f"[AGENT DONE] user={user_id} elapsed={time.time() - start:.1f}s answer_len={len(answer or '')}")
        response = push_message(user_id, answer or "查詢完成，但沒有產生內容。")
        if response.status_code < 400:
            log_debug(f"[PUSH DONE] user={user_id} elapsed={time.time() - start:.1f}s")
        else:
            log_debug(
                f"[PUSH FAILED] user={user_id} elapsed={time.time() - start:.1f}s "
                f"status={response.status_code} body={response.text}"
            )
    except Exception as e:
        log_debug(f"[AGENT ERROR] user={user_id} elapsed={time.time() - start:.1f}s error={e}\n{traceback.format_exc()}")
        try:
            push_message(user_id, f"查詢失敗：{e}")
        except Exception as push_error:
            log_debug(f"[PUSH ERROR] user={user_id} error={push_error}\n{traceback.format_exc()}")


def run_agent_to_page(user_id: str, prompt: str, page_path: str):
    start = time.time()
    log_debug(f"[AGENT PAGE START] user={user_id} backend={AGENT_BACKEND} page={page_path} prompt={prompt[:80]!r}")
    try:
        answer = ask_agent(prompt, allow_write=False)
        log_debug(f"[AGENT PAGE DONE] user={user_id} elapsed={time.time() - start:.1f}s answer_len={len(answer or '')}")
        write_answer_page(page_path, "query", prompt, "完成", answer or "查詢完成，但沒有產生內容。")
    except Exception as e:
        log_debug(f"[AGENT PAGE ERROR] user={user_id} elapsed={time.time() - start:.1f}s error={e}\n{traceback.format_exc()}")
        write_answer_page(page_path, "query", prompt, "失敗", f"查詢失敗：{e}")


def run_report_and_push(user_id: str, report_text: str):
    start = time.time()
    log_debug(f"[REPORT START] user={user_id} backend={AGENT_BACKEND} text={report_text[:80]!r}")
    try:
        prompt = (
            "幫我新增到筆記，以下是問題回報內容。\n"
            "請沿用目前 vault 既有寫入規則與品質標準處理。\n\n"
            f"{report_text}"
        )
        answer = ask_agent(prompt, allow_write=True)
        log_debug(f"[REPORT DONE] user={user_id} elapsed={time.time() - start:.1f}s answer_len={len(answer or '')}")
        response = push_message(user_id, format_report_result(answer or "整理完成，但沒有產生內容。"))
        if response.status_code < 400:
            log_debug(f"[REPORT PUSH DONE] user={user_id} elapsed={time.time() - start:.1f}s")
        else:
            log_debug(
                f"[REPORT PUSH FAILED] user={user_id} elapsed={time.time() - start:.1f}s "
                f"status={response.status_code} body={response.text}"
            )
    except Exception as e:
        log_debug(f"[REPORT ERROR] user={user_id} elapsed={time.time() - start:.1f}s error={e}\n{traceback.format_exc()}")
        try:
            push_message(user_id, f"寫入失敗：{e}")
        except Exception as push_error:
            log_debug(f"[REPORT PUSH ERROR] user={user_id} error={push_error}\n{traceback.format_exc()}")


def run_report_to_page(user_id: str, report_text: str, page_path: str):
    start = time.time()
    log_debug(f"[REPORT PAGE START] user={user_id} backend={AGENT_BACKEND} page={page_path} text={report_text[:80]!r}")
    try:
        prompt = (
            "幫我新增到筆記，以下是問題回報內容。\n"
            "請沿用目前 vault 既有寫入規則與品質標準處理。\n\n"
            f"{report_text}"
        )
        answer = ask_agent(prompt, allow_write=True)
        log_debug(f"[REPORT PAGE DONE] user={user_id} elapsed={time.time() - start:.1f}s answer_len={len(answer or '')}")
        write_answer_page(page_path, "report", report_text, "完成", format_report_result(answer or "整理完成，但沒有產生內容。"))
    except Exception as e:
        log_debug(f"[REPORT PAGE ERROR] user={user_id} elapsed={time.time() - start:.1f}s error={e}\n{traceback.format_exc()}")
        write_answer_page(page_path, "report", report_text, "失敗", f"寫入失敗：{e}")


def format_report_result(answer: str) -> str:
    saved_path = extract_saved_path(answer)
    if not saved_path:
        return answer

    filename = os.path.basename(saved_path)
    lines = [
        "已記錄到 vault",
        f"檔名：{filename}",
        f"路徑：{saved_path}",
    ]
    return "\n".join(lines)


def extract_saved_path(answer: str) -> str:
    path_patterns = [
        r"00_Inbox[\\/][^\s`\"'，。]+\.md",
        r"[A-Za-z]:[\\/][^\r\n`\"']+?\.md",
    ]
    for pattern in path_patterns:
        match = re.search(pattern, answer)
        if not match:
            continue
        path = match.group(0).strip()
        return path.replace("/", "\\")
    return ""


def handle_postback(reply_token: str, user_id: str, data: str):
    action, params = parse_postback(data)
    if action == "cancel_report":
        clear_user_mode(user_id)
        reply_message(reply_token, "已取消問題回報，回到一般查詢模式。")
        return
    if action == "direct_input":
        clear_user_mode(user_id)
        reply_message(reply_token, "已切換到直接輸入模式，請直接輸入查詢內容。")
        return
    if action == "daily_report":
        clear_user_mode(user_id)
        reply_messages(reply_token, [build_daily_report_message()])
        return
    if action == "query_projects":
        clear_user_mode(user_id)
        page = int(params.get("page", "0") or 0)
        page = max(page, 0)
        reply_messages(
            reply_token,
            [
                {"type": "text", "text": build_home_text()},
                build_project_list_flex(page),
            ],
        )
        return
    if action == "project_summary":
        clear_user_mode(user_id)
        project_name = params.get("project", "")
        reply_messages(
            reply_token,
            [
                build_project_summary(project_name),
                {"type": "text", "text": build_home_text()},
            ],
        )
        return
    reply_message(reply_token, build_home_text())


@app.post("/webhook")
async def webhook(request: Request):
    global LAST_REQUEST_BASE_URL
    LAST_REQUEST_BASE_URL = get_request_base_url(request)
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = await request.json() if not body else __import__("json").loads(body)

    for event in data.get("events", []):
        event_type = event.get("type")
        if event_type not in {"message", "postback"}:
            continue

        user_id = event["source"]["userId"]
        reply_token = event["replyToken"]

        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            print(f"[BLOCKED] user_id={user_id}")
            continue

        if event_type == "postback":
            handle_postback(reply_token, user_id, event.get("postback", {}).get("data", ""))
            continue

        if event.get("message", {}).get("type") != "text":
            continue

        user_text = event["message"]["text"].strip()
        if not user_text:
            reply_messages(reply_token, [build_home_menu_message()])
            continue

        if user_text.lower() in {"選單", "menu", "功能", "功能選單"}:
            clear_user_mode(user_id)
            reply_messages(reply_token, [build_home_menu_message()])
            continue

        if user_text in {"取消", "取消回報"}:
            clear_user_mode(user_id)
            reply_message(reply_token, "已取消問題回報，回到一般查詢模式。")
            continue

        if user_text == "查詢專案":
            clear_user_mode(user_id)
            reply_messages(
                reply_token,
                [
                    {"type": "text", "text": build_home_text()},
                    build_project_list_flex(0),
                ],
            )
            continue

        if user_text == "回報問題":
            start_report_mode(reply_token, user_id)
            continue

        if user_text == "直接輸入":
            clear_user_mode(user_id)
            reply_message(reply_token, "已切換到直接輸入模式，請直接輸入查詢內容。")
            continue

        if user_text in {"今日 Daily Report", "今日Daily Report", "查詢今日daily report", "查詢今日 Daily Report"}:
            clear_user_mode(user_id)
            reply_messages(reply_token, [build_daily_report_message()])
            continue

        had_mode = user_id in USER_MODES
        mode = get_user_mode(user_id)
        if had_mode and not mode:
            page_path = make_answer_page("query", user_text)
            reply_messages(
                reply_token,
                [
                    {"type": "text", "text": "問題回報模式已超過 5 分鐘自動取消。這次改用一般查詢處理。"},
                    build_answer_page_message(page_path, user_text, "query"),
                ],
            )
            threading.Thread(
                target=run_agent_to_page,
                args=(user_id, user_text, page_path),
                daemon=True,
            ).start()
            continue

        if mode == "report":
            clear_user_mode(user_id)
            page_path = make_answer_page("report", user_text)
            reply_messages(reply_token, [build_answer_page_message(page_path, user_text, "report")])
            threading.Thread(
                target=run_report_to_page,
                args=(user_id, user_text, page_path),
                daemon=True,
            ).start()
            continue

        input_mode, normalized_text = parse_input_mode(user_text)
        if not normalized_text:
            reply_message(reply_token, "請在模式後輸入內容。")
            continue
        if input_mode == "report":
            page_path = make_answer_page("report", normalized_text)
            reply_messages(reply_token, [build_answer_page_message(page_path, normalized_text, "report")])
            threading.Thread(
                target=run_report_to_page,
                args=(user_id, normalized_text, page_path),
                daemon=True,
            ).start()
            continue

        page_path = make_answer_page("query", normalized_text)
        reply_messages(reply_token, [build_answer_page_message(page_path, normalized_text, "query")])
        threading.Thread(
            target=run_agent_to_page,
            args=(user_id, normalized_text, page_path),
            daemon=True,
        ).start()

    return "OK"
