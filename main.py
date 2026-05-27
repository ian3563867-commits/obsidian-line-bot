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
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, quote, urlencode

import markdown
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, Response

from ask_claude import ask_claude
from ask_codex import ask_codex
from retrieval import build_candidate_search_prompt, build_index_hint_prompt, build_preloaded_prompt, run_precheck

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
TODO_ITEMS_PER_BUBBLE = 5
TODO_MAX_CAROUSEL_BUBBLES = 12
USER_MODES: dict[str, dict] = {}
DISCUSSION_FILE_LOCK = threading.RLock()
TODO_FILE_LOCK = threading.RLock()
TODO_TASKS_PATH = os.environ.get("TODO_TASKS_PATH", "").strip()
TODO_DASHBOARD_TOKEN = os.environ.get("TODO_DASHBOARD_TOKEN", "").strip()
LAST_REQUEST_BASE_URL = ""
LOG_FILE = os.environ.get("BOT_LOG_FILE", os.path.join(os.path.dirname(__file__), "bot-debug.log"))
APP_ASSETS_DIR = os.environ.get("APP_ASSETS_DIR", os.path.join(os.path.dirname(__file__), "assets"))
MIND_PALACE_ICON_PATH = os.path.join(APP_ASSETS_DIR, "mind-palace-icon.png")
CARD_BG = "#F8FAF4"
CARD_PANEL_BG = "#FFFFFF"
CARD_INK = "#17211B"
CARD_MUTED = "#6C7A70"
CARD_SUBTLE = "#DCE7DD"
CARD_ACCENT = "#06C755"
CARD_ACCENT_DARK = "#048F3D"
CARD_FOOTER_BG = "#F1F7EF"

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


def ask_agent(prompt: str, allow_write: bool = False, life_override: bool = False) -> str:
    if AGENT_BACKEND == "claude":
        return ask_claude(prompt, allow_write=allow_write, life_override=life_override)
    if AGENT_BACKEND == "codex":
        return ask_codex(prompt, allow_write=allow_write, life_override=life_override)
    return f"未知 AGENT_BACKEND={AGENT_BACKEND}，請設定為 claude 或 codex。"


def call_ask_agent(prompt: str, allow_write: bool = False, life_override: bool = False) -> str:
    try:
        return ask_agent(prompt, allow_write=allow_write, life_override=life_override)
    except TypeError as exc:
        if "life_override" not in str(exc):
            raise
        return ask_agent(prompt, allow_write=allow_write)


def answer_query(prompt: str) -> tuple[str, str]:
    precheck = run_precheck(prompt, VAULT_DIR)
    if precheck.hit:
        log_debug(
            f"[PRECHECK HIT] prompt={prompt[:80]!r} "
            f"mode={precheck.mode} life_override={precheck.life_override} "
            f"blocks={len(precheck.source_blocks)} entries={len(precheck.matched_entries)}"
        )
        source = "manifest" if precheck.mode == "manifest_candidates" else "precheck"
        return (
            call_ask_agent(
                build_preloaded_prompt(prompt, precheck),
                allow_write=False,
                life_override=precheck.life_override,
            ),
            source,
        )
    if precheck.mode == "candidate_list":
        log_debug(
            f"[PRECHECK CANDIDATES] prompt={prompt[:80]!r} "
            f"entries={len(precheck.matched_entries)} reason={precheck.fallback_reason}"
        )
        return call_ask_agent(build_candidate_search_prompt(prompt, precheck), allow_write=False), "candidate_search"
    if precheck.mode == "index_hints":
        log_debug(
            f"[PRECHECK HINTS] prompt={prompt[:80]!r} "
            f"entries={len(precheck.matched_entries)} reason={precheck.fallback_reason}"
        )
        return call_ask_agent(build_index_hint_prompt(prompt, precheck), allow_write=False), "index_hints"
    log_debug(f"[PRECHECK FALLBACK] prompt={prompt[:80]!r} reason={precheck.fallback_reason}")
    return call_ask_agent(prompt, allow_write=False), "fallback"


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


def get_todo_tasks_path() -> str:
    return TODO_TASKS_PATH or os.path.join(VAULT_DIR, "06_System", "ToDo", "tasks.md")


def escape_markdown_table_cell(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def todo_content_preview(content: str, limit: int = 42) -> str:
    normalized = " ".join(str(content or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def get_todo_dashboard_url_hint() -> str:
    base_url = OPEN_NOTE_BASE_URL or LAST_REQUEST_BASE_URL.rstrip("/")
    if not base_url:
        return "/todos?token=<TODO_DASHBOARD_TOKEN>"
    return base_url + "/todos?token=<TODO_DASHBOARD_TOKEN>"


def todo_empty_file_content() -> str:
    return """---
title: LINE Bot To-do Tasks
date: {date}
tags: [LINEBot, ToDo, System]
project: 9002-VaultLINEBot
---

# LINE Bot To-do Tasks

此檔由 LINE Bot To-do 功能維護，請避免手動重排任務區塊。
""".format(date=datetime.now().strftime("%Y-%m-%d"))


def ensure_todo_tasks_file() -> str:
    path = get_todo_tasks_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(todo_empty_file_content())
    return path


def parse_todo_task_block(task_id: str, block: str) -> dict:
    task = {
        "id": task_id,
        "status": "open",
        "type": "work",
        "project": "",
        "content": "",
        "owner": "",
        "due": "",
        "created_at": "",
        "updated_at": "",
        "source": "line_bot",
        "reports": [],
    }
    content_match = re.search(r"### 內容\n(.*?)(?=\n### |\Z)", block, flags=re.S)
    if content_match:
        task["content"] = content_match.group(1).strip()
    reports_match = re.search(r"### 回報紀錄\n(.*?)(?=\n## T\d{8}-\d{3}\b|\Z)", block, flags=re.S)
    if reports_match:
        reports = []
        for line in reports_match.group(1).splitlines():
            match = re.match(r"-\s+(.+?)：(.+)", line.strip())
            if match:
                reports.append({"created_at": match.group(1).strip(), "content": match.group(2).strip()})
        task["reports"] = reports
    for line in block.splitlines():
        match = re.match(r"-\s+([a-z_]+):\s*(.*)", line.strip())
        if match and match.group(1) in task:
            task[match.group(1)] = match.group(2).strip()
    return task


def load_todo_tasks() -> list[dict]:
    path = ensure_todo_tasks_file()
    with TODO_FILE_LOCK:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    tasks = []
    matches = list(re.finditer(r"^## (T\d{8}-\d{3})\s*$", content, flags=re.M))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        tasks.append(parse_todo_task_block(match.group(1), content[start:end]))
    return tasks


def serialize_todo_task(task: dict) -> str:
    reports = task.get("reports") or []
    report_lines = ["尚無回報紀錄"] if not reports else [
        f"- {report.get('created_at', '')}：{report.get('content', '')}" for report in reports
    ]
    return "\n".join(
        [
            f"## {task['id']}",
            "",
            f"- status: {task.get('status', 'open')}",
            f"- type: {task.get('type', 'work')}",
            f"- project: {task.get('project', '')}",
            f"- owner: {task.get('owner', '')}",
            f"- due: {task.get('due', '')}",
            f"- created_at: {task.get('created_at', '')}",
            f"- updated_at: {task.get('updated_at', '')}",
            f"- source: {task.get('source', 'line_bot')}",
            "",
            "### 內容",
            task.get("content", "").strip(),
            "",
            "### 回報紀錄",
            *report_lines,
            "",
        ]
    )


def build_todo_markdown_overview(tasks: list[dict]) -> str:
    today = date.today().isoformat()
    open_tasks = [task for task in tasks if task.get("status") == "open"]
    due_today_or_overdue = [task for task in open_tasks if task.get("due") and task.get("due") <= today]
    due_later = [task for task in open_tasks if task.get("due") and task.get("due") > today]
    no_due = [task for task in open_tasks if not task.get("due")]
    done_count = len([task for task in tasks if task.get("status") == "done"])
    deleted_count = len([task for task in tasks if task.get("status") == "deleted"])

    lines = [
        "## 待辦總覽",
        "",
        f"- 未完成：{len(open_tasks)}",
        f"- 今日 / 逾期：{len(due_today_or_overdue)}",
        f"- 有期限未完成：{len(due_today_or_overdue) + len(due_later)}",
        f"- 無期限未完成：{len(no_due)}",
        f"- 已完成：{done_count}",
        f"- 已刪除：{deleted_count}",
        f"- Dashboard：`{get_todo_dashboard_url_hint()}`",
        "",
        "Dashboard 入口由主機上的 9002 FastAPI 提供；筆電請使用 ngrok 網址加 `TODO_DASHBOARD_TOKEN` 開啟。",
        "",
    ]

    sections = [
        ("今日 / 逾期", sort_todo_tasks(due_today_or_overdue)),
        ("有期限", sort_todo_tasks(due_later)),
        ("無期限", sort_todo_tasks(no_due)),
    ]
    for title, section_tasks in sections:
        lines.extend([f"### {title}", ""])
        if not section_tasks:
            lines.extend(["目前沒有任務。", ""])
            continue
        lines.extend(["| 期限 | 專案 | 內容 | ID |", "|---|---|---|---|"])
        for task in section_tasks:
            lines.append(
                "| {due} | {project} | {content} | `{task_id}` |".format(
                    due=escape_markdown_table_cell(task.get("due") or "-"),
                    project=escape_markdown_table_cell(task.get("project") or "-"),
                    content=escape_markdown_table_cell(todo_content_preview(task.get("content", ""))),
                    task_id=escape_markdown_table_cell(task.get("id", "")),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## 機器資料區",
            "",
            "下方區塊供 LINE Bot 讀寫，請不要手動重排任務區塊。",
        ]
    )
    return "\n".join(lines).rstrip()


def save_todo_tasks(tasks: list[dict]):
    path = ensure_todo_tasks_file()
    tmp_path = path + ".tmp"
    content = todo_empty_file_content().rstrip() + "\n\n"
    content += build_todo_markdown_overview(tasks) + "\n\n"
    content += "\n".join(serialize_todo_task(task) for task in tasks)
    with TODO_FILE_LOCK:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)


def parse_todo_due(text: str) -> str:
    today = date.today()
    normalized = text.strip()
    if "明天" in text:
        return (today + timedelta(days=1)).isoformat()
    if "後天" in text:
        return (today + timedelta(days=2)).isoformat()
    if "今天" in text or "今日" in text:
        return today.isoformat()
    if "本週" in text or "這週" in text or "這禮拜" in text:
        return (today + timedelta(days=6 - today.weekday())).isoformat()
    if "下週" in text or "下禮拜" in text:
        return (today + timedelta(days=13 - today.weekday())).isoformat()
    if "月底" in text:
        next_month = today.replace(day=28) + timedelta(days=4)
        return (next_month - timedelta(days=next_month.day)).isoformat()
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if match:
        return match.group(1)
    match = re.search(r"\b(20\d{2})[/.](\d{1,2})[/.](\d{1,2})\b", normalized)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
        except ValueError:
            return ""
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", normalized)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        try:
            parsed = date(today.year, month, day)
            if parsed < today:
                parsed = date(today.year + 1, month, day)
            return parsed.isoformat()
        except ValueError:
            return ""
    return ""


def infer_todo_type(text: str) -> str:
    life_keywords = ("買", "繳費", "家庭", "家裡", "小孩", "老婆", "運動", "看醫生", "旅行", "保險", "信用卡")
    return "life" if any(keyword in text for keyword in life_keywords) else "work"


def infer_todo_project(text: str) -> str:
    project_keywords = {
        "9002-VaultLINEBot": ("LINEBot", "LINE Bot", "line bot", "vault", "mind palace", "To-do", "todo", "待辦"),
        "0188-SampleProjectA": ("SampleProjectA", "Site1", "SampleCorpA"),
        "0182-SampleProjectB": ("SampleProjectB", "SampleCorpB", "SampleProjectB WMS"),
        "SampleProjectD": ("SampleProjectD", "SampleProjectD ASRS"),
        "WMS": ("WMS", "入庫", "出貨", "調撥", "庫存"),
        "ASRS": ("ASRS", "自動倉", "高架倉"),
        "LCS": ("LCS", "輸送線", "PLC"),
    }
    for project, keywords in project_keywords.items():
        if any(keyword in text for keyword in keywords):
            return project
    projects_dir = os.path.join(VAULT_DIR, "02_Projects")
    if os.path.isdir(projects_dir):
        for name in os.listdir(projects_dir):
            path = os.path.join(projects_dir, name)
            if not os.path.isdir(path):
                continue
            aliases = {name, re.sub(r"^\d{4}-", "", name)}
            aliases.update(part for part in re.split(r"[-_ ]+", name) if len(part) >= 2)
            if any(alias and alias in text for alias in aliases):
                return name
    return ""


def next_todo_id(tasks: list[dict]) -> str:
    prefix = "T" + datetime.now().strftime("%Y%m%d")
    max_seq = 0
    for task in tasks:
        task_id = str(task.get("id", ""))
        match = re.fullmatch(prefix + r"-(\d{3})", task_id)
        if match:
            max_seq = max(max_seq, int(match.group(1)))
    return f"{prefix}-{max_seq + 1:03d}"


def create_todo_task(content: str) -> dict:
    with TODO_FILE_LOCK:
        tasks = load_todo_tasks()
        now = now_text()
        task = {
            "id": next_todo_id(tasks),
            "status": "open",
            "type": infer_todo_type(content),
            "project": infer_todo_project(content),
            "content": content.strip(),
            "owner": "maintainer",
            "due": parse_todo_due(content),
            "created_at": now,
            "updated_at": now,
            "source": "line_bot",
            "reports": [],
        }
        tasks.append(task)
        save_todo_tasks(tasks)
        return task


def find_todo_task(task_id: str) -> dict | None:
    for task in load_todo_tasks():
        if task.get("id") == task_id:
            return task
    return None


def update_todo_task_status(task_id: str, status: str) -> dict | None:
    with TODO_FILE_LOCK:
        tasks = load_todo_tasks()
        target = None
        for task in tasks:
            if task.get("id") == task_id:
                task["status"] = status
                task["updated_at"] = now_text()
                target = task
                break
        if target:
            save_todo_tasks(tasks)
        return target


def append_todo_report(task_id: str, report_text: str) -> dict | None:
    with TODO_FILE_LOCK:
        tasks = load_todo_tasks()
        target = None
        for task in tasks:
            if task.get("id") == task_id:
                task.setdefault("reports", []).append({"created_at": now_text(), "content": report_text.strip()})
                task["updated_at"] = now_text()
                target = task
                break
        if target:
            save_todo_tasks(tasks)
        return target


def sort_todo_tasks(tasks: list[dict]) -> list[dict]:
    def sort_key(task: dict):
        due = task.get("due") or "9999-12-31"
        updated = task.get("updated_at") or ""
        return due, updated

    return sorted(tasks, key=sort_key)


def todo_dashboard_sort_key(task: dict):
    status_order = {"open": 0, "done": 1, "deleted": 2}
    due = task.get("due") or "9999-12-31"
    updated = task.get("updated_at") or ""
    return status_order.get(task.get("status"), 9), due, updated


def build_todo_dashboard_stats(tasks: list[dict]) -> dict:
    today = date.today().isoformat()
    open_tasks = [task for task in tasks if task.get("status") == "open"]
    return {
        "total": len(tasks),
        "open": len(open_tasks),
        "today_or_overdue": len([task for task in open_tasks if task.get("due") and task.get("due") <= today]),
        "with_due": len([task for task in open_tasks if task.get("due")]),
        "without_due": len([task for task in open_tasks if not task.get("due")]),
        "done": len([task for task in tasks if task.get("status") == "done"]),
        "deleted": len([task for task in tasks if task.get("status") == "deleted"]),
    }


def todo_task_to_api(task: dict) -> dict:
    reports = task.get("reports") or []
    return {
        "id": task.get("id", ""),
        "status": task.get("status", "open"),
        "status_label": build_todo_status_label(task.get("status", "open")),
        "type": task.get("type", "work"),
        "project": task.get("project", ""),
        "owner": task.get("owner", ""),
        "due": task.get("due", ""),
        "created_at": task.get("created_at", ""),
        "updated_at": task.get("updated_at", ""),
        "source": task.get("source", "line_bot"),
        "content": task.get("content", ""),
        "reports": reports,
        "latest_report": reports[-1] if reports else None,
        "is_due": bool(task.get("due") and task.get("due") <= date.today().isoformat()),
    }


def get_open_todo_tasks(scope: str = "all") -> list[dict]:
    tasks = [task for task in load_todo_tasks() if task.get("status") == "open"]
    if scope == "today":
        today = date.today().isoformat()
        tasks = [task for task in tasks if task.get("due") and task.get("due") <= today]
    return sort_todo_tasks(tasks)


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
            "styles": build_bubble_styles(),
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "backgroundColor": CARD_BG,
                "contents": [
                    build_card_header(title),
                    {
                        "type": "text",
                        "text": "結果頁已建立",
                        "size": "xl",
                        "weight": "bold",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": created_at,
                        "size": "xs",
                        "color": CARD_MUTED,
                        "wrap": True,
                    },
                    {
                        "type": "separator",
                        "margin": "lg",
                        "color": CARD_SUBTLE,
                    },
                    build_info_row(prompt_label, shorten_label(prompt, 72)),
                ],
            },
            "footer": build_card_footer(
                [
                    build_uri_button("開啟", build_note_open_url(path)),
                ]
            ),
        },
        "quickReply": build_home_quick_reply(),
    }


def build_bubble_styles() -> dict:
    return {
        "body": {"backgroundColor": CARD_BG},
        "footer": {"backgroundColor": CARD_FOOTER_BG},
    }


def build_card_header(label: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "spacing": "sm",
        "paddingAll": "10px",
        "backgroundColor": CARD_PANEL_BG,
        "cornerRadius": "14px",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "sm",
                "weight": "bold",
                "color": CARD_ACCENT_DARK,
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


def build_info_row(label: str, value: str) -> dict:
    return {
        "type": "box",
        "layout": "horizontal",
        "margin": "md",
        "paddingAll": "12px",
        "backgroundColor": CARD_PANEL_BG,
        "cornerRadius": "12px",
        "contents": [
            {
                "type": "text",
                "text": label,
                "size": "sm",
                "color": CARD_MUTED,
                "flex": 1,
            },
            {
                "type": "text",
                "text": value,
                "size": "sm",
                "color": CARD_INK,
                "align": "end",
                "wrap": True,
                "flex": 2,
            },
        ],
    }


def build_card_footer(contents: list[dict]) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "paddingTop": "14px",
        "paddingStart": "20px",
        "paddingEnd": "20px",
        "paddingBottom": "20px",
        "contents": contents,
    }


def build_message_button(label: str, text: str, style: str = "secondary") -> dict:
    button = {
        "type": "button",
        "style": style,
        "height": "sm",
        "action": {
            "type": "message",
            "label": label,
            "text": text,
        },
    }
    if style == "primary":
        button["color"] = CARD_ACCENT
    return button


def build_uri_button(label: str, uri: str, style: str = "primary") -> dict:
    button = {
        "type": "button",
        "style": style,
        "height": "sm",
        "action": {
            "type": "uri",
            "label": label,
            "uri": uri,
        },
    }
    if style == "primary":
        button["color"] = CARD_ACCENT
    return button


def build_postback_button(
    label: str,
    data: str,
    display_text: str,
    style: str = "primary",
) -> dict:
    button = {
        "type": "button",
        "style": style,
        "height": "sm",
        "action": {
            "type": "postback",
            "label": label,
            "data": data,
            "displayText": display_text,
        },
    }
    if style == "primary":
        button["color"] = CARD_ACCENT
    return button


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


def verify_todo_dashboard_token(token: str):
    if not TODO_DASHBOARD_TOKEN:
        raise HTTPException(status_code=503, detail="TODO_DASHBOARD_TOKEN is not configured")
    if not hmac.compare_digest(str(token or ""), TODO_DASHBOARD_TOKEN):
        raise HTTPException(status_code=403, detail="Invalid dashboard token")


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
                    "label": "To-do",
                    "text": "To-do",
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
                    "label": "全部待辦",
                    "text": "全部待辦",
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
            "size": "mega",
            "styles": build_bubble_styles(),
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "backgroundColor": CARD_BG,
                "contents": [
                    build_card_header("Mind Palace"),
                    {
                        "type": "text",
                        "text": "功能選單",
                        "weight": "bold",
                        "size": "xl",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": "電腦版 LINE 可用下方按鈕操作；手機版也可以繼續使用 Rich Menu。",
                        "size": "sm",
                        "color": CARD_MUTED,
                        "wrap": True,
                    },
                ],
            },
            "footer": build_card_footer(
                [
                    build_message_button("查詢專案", "查詢專案", "primary"),
                    build_message_button("回報問題", "回報問題"),
                    build_message_button("Daily Report", "今日 Daily Report"),
                    build_message_button("To-do", "To-do"),
                    build_message_button("今日待辦", "今日待辦"),
                    build_message_button("全部待辦", "全部待辦"),
                ]
            ),
        },
        "quickReply": build_home_quick_reply(),
    }


def set_user_mode(user_id: str, mode: str, **fields):
    USER_MODES[user_id] = {"mode": mode, "started_at": time.time(), **fields}


def clear_user_mode(user_id: str):
    USER_MODES.pop(user_id, None)


def get_user_mode_state(user_id: str) -> dict:
    state = USER_MODES.get(user_id)
    if not state:
        return {}
    if state.get("mode") in {"report", "todo_create", "todo_report"}:
        started_at = float(state.get("started_at", 0))
        if time.time() - started_at > REPORT_MODE_TIMEOUT_SECONDS:
            clear_user_mode(user_id)
            return {}
    return state


def get_user_mode(user_id: str) -> str:
    return str(get_user_mode_state(user_id).get("mode", ""))


def user_mode_timeout_text(expired_mode: str) -> str:
    if expired_mode in {"todo_create", "todo_report"}:
        return "待辦操作已超過 5 分鐘自動取消。"
    return "問題回報模式已超過 5 分鐘自動取消。這次改用一般查詢處理。"


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
                "size": "mega",
                "styles": build_bubble_styles(),
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "md",
                    "paddingAll": "20px",
                    "backgroundColor": CARD_BG,
                    "contents": [
                        build_card_header("Knowledge 摘要"),
                        {
                            "type": "text",
                            "text": "目前找不到 Knowledge 資料夾。",
                            "size": "md",
                            "color": CARD_INK,
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
            "color": CARD_MUTED,
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
                "paddingAll": "12px",
                "backgroundColor": CARD_PANEL_BG,
                "cornerRadius": "12px",
                "contents": [
                    {
                        "type": "text",
                        "text": name,
                        "weight": "bold",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    build_postback_button(
                        "查看摘要",
                        "action=project_summary&"
                        + urlencode({"project": name}, quote_via=quote),
                        f"查看知識：{name}",
                    ),
                ],
            }
        )

    return {
        "type": "bubble",
        "size": "mega",
        "styles": build_bubble_styles(),
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "20px",
            "backgroundColor": CARD_BG,
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
            "color": CARD_INK,
            "wrap": True,
        },
        {
            "type": "text",
            "text": "最近 Knowledge 筆記",
            "size": "sm",
            "color": CARD_MUTED,
            "margin": "sm",
        },
    ]
    if total_pages > 1:
        contents.append(
            {
                "type": "text",
                "text": f"第 {page + 1} / {total_pages} 頁",
                "size": "xs",
                "color": CARD_MUTED,
                "margin": "xs",
            }
        )
    if not recent_notes:
        contents.append(
            {
                "type": "text",
                "text": "目前沒有 markdown 筆記",
                "size": "md",
                "color": CARD_INK,
                "margin": "lg",
                "wrap": True,
            }
        )
    else:
        for path in recent_notes:
            title = os.path.splitext(os.path.basename(path))[0]
            contents.append(
                build_uri_button(shorten_label(title), build_note_open_url(path), "secondary")
            )
            contents[-1]["margin"] = "md"

    return {
        "type": "bubble",
        "size": "mega",
        "styles": build_bubble_styles(),
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "20px",
            "backgroundColor": CARD_BG,
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
            "size": "mega",
            "styles": build_bubble_styles(),
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "backgroundColor": CARD_BG,
                "contents": [
                    build_card_header("Daily Report"),
                    {
                        "type": "text",
                        "text": status_text,
                        "size": "xl",
                        "weight": "bold",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": title,
                        "size": "xs",
                        "color": CARD_MUTED,
                        "wrap": True,
                    },
                    {
                        "type": "separator",
                        "margin": "lg",
                        "color": CARD_SUBTLE,
                    },
                    build_info_row(detail_label, detail_text),
                ],
            },
            "footer": build_card_footer(
                [
                    build_uri_button("開啟", build_note_open_url(target_path)),
                ]
            ),
        },
        "quickReply": build_home_quick_reply(),
    }


def build_todo_status_label(status: str) -> str:
    labels = {"open": "未完成", "done": "已完成", "deleted": "已刪除"}
    return labels.get(status, status or "未完成")


def build_todo_summary(task: dict) -> str:
    due = task.get("due") or "無期限"
    return f"{task.get('id')}｜{due}｜{shorten_label(task.get('content', ''), 48)}"


def build_todo_list_bubble(title: str, tasks: list[dict], page: int, total_pages: int, total_count: int) -> dict:
    contents: list[dict] = [
        build_card_header(title),
        {
            "type": "text",
            "text": f"第 {page + 1} / {total_pages} 頁，共 {total_count} 筆未完成",
            "size": "sm",
            "color": CARD_MUTED,
            "wrap": True,
        },
    ]
    for task in tasks:
        contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "paddingAll": "12px",
                "backgroundColor": CARD_PANEL_BG,
                "cornerRadius": "12px",
                "contents": [
                    {
                        "type": "text",
                        "text": build_todo_summary(task),
                        "size": "sm",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    build_postback_button(
                        "查看",
                        "action=todo_detail&" + urlencode({"task_id": task.get("id", "")}, quote_via=quote),
                        f"查看待辦 {task.get('id', '')}",
                        "secondary",
                    ),
                ],
            }
        )

    return {
        "type": "bubble",
        "size": "mega",
        "styles": build_bubble_styles(),
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "20px",
            "backgroundColor": CARD_BG,
            "contents": contents,
        },
    }


def build_todo_detail_message(task: dict) -> dict:
    reports = task.get("reports") or []
    latest_report = "尚無回報"
    if reports:
        latest = reports[-1]
        latest_report = f"{latest.get('created_at', '')}：{latest.get('content', '')}"
    return {
        "type": "flex",
        "altText": f"待辦 {task.get('id')}",
        "contents": {
            "type": "bubble",
            "size": "mega",
            "styles": build_bubble_styles(),
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "backgroundColor": CARD_BG,
                "contents": [
                    build_card_header("To-do"),
                    {
                        "type": "text",
                        "text": task.get("content", ""),
                        "size": "xl",
                        "weight": "bold",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": task.get("id", ""),
                        "size": "xs",
                        "color": CARD_MUTED,
                        "wrap": True,
                    },
                    {"type": "separator", "margin": "lg", "color": CARD_SUBTLE},
                    build_info_row("狀態", build_todo_status_label(task.get("status", "open"))),
                    build_info_row("期限", task.get("due") or "無期限"),
                    build_info_row("專案", task.get("project") or "未指定"),
                    build_info_row("負責人", task.get("owner") or "未指定"),
                    build_info_row("最近回報", shorten_label(latest_report, 90)),
                ],
            },
            "footer": build_card_footer(
                [
                    build_postback_button(
                        "回報",
                        "action=todo_report&" + urlencode({"task_id": task.get("id", "")}, quote_via=quote),
                        f"回報待辦 {task.get('id', '')}",
                    ),
                    build_postback_button(
                        "完成",
                        "action=todo_done&" + urlencode({"task_id": task.get("id", "")}, quote_via=quote),
                        f"完成待辦 {task.get('id', '')}",
                        "secondary",
                    ),
                    build_postback_button(
                        "刪除",
                        "action=todo_delete&" + urlencode({"task_id": task.get("id", "")}, quote_via=quote),
                        f"刪除待辦 {task.get('id', '')}",
                        "secondary",
                    ),
                ]
            ),
        },
        "quickReply": build_home_quick_reply(),
    }


def build_todo_created_message(task: dict) -> dict:
    message = build_todo_detail_message(task)
    message["altText"] = f"待辦已建立 {task.get('id')}"
    message["contents"]["body"]["contents"][0] = build_card_header("To-do 已建立")
    return message


def build_todo_report_mode_message(task: dict) -> dict:
    return {
        "type": "flex",
        "altText": "待辦回報模式",
        "contents": {
            "type": "bubble",
            "size": "mega",
            "styles": build_bubble_styles(),
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "paddingAll": "20px",
                "backgroundColor": CARD_BG,
                "contents": [
                    build_card_header("待辦回報"),
                    {
                        "type": "text",
                        "text": "請輸入最新進度",
                        "size": "xl",
                        "weight": "bold",
                        "color": CARD_INK,
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": shorten_label(task.get("content", ""), 72),
                        "size": "sm",
                        "color": CARD_MUTED,
                        "wrap": True,
                    },
                    {"type": "separator", "margin": "lg", "color": CARD_SUBTLE},
                    build_info_row("ID", task.get("id", "")),
                    build_info_row("有效時間", "5 分鐘"),
                ],
            },
            "footer": build_card_footer(
                [
                    build_postback_button(
                        "取消",
                        "action=cancel_todo",
                        "取消待辦回報",
                        "secondary",
                    ),
                ]
            ),
        },
        "quickReply": build_home_quick_reply(),
    }


def build_todo_list_message(scope: str = "all") -> dict:
    tasks = get_open_todo_tasks(scope)
    title = "今日待辦" if scope == "today" else "全部待辦"
    if not tasks:
        empty_text = "今天沒有到期或逾期的待辦。" if scope == "today" else "目前沒有未完成待辦。"
        return {"type": "text", "text": empty_text, "quickReply": build_home_quick_reply()}

    visible_tasks = tasks[: TODO_ITEMS_PER_BUBBLE * TODO_MAX_CAROUSEL_BUBBLES]
    total_pages = max((len(visible_tasks) - 1) // TODO_ITEMS_PER_BUBBLE + 1, 1)
    bubbles = []
    for page_index in range(total_pages):
        start = page_index * TODO_ITEMS_PER_BUBBLE
        page_tasks = visible_tasks[start : start + TODO_ITEMS_PER_BUBBLE]
        bubbles.append(build_todo_list_bubble(title, page_tasks, page_index, total_pages, len(tasks)))
    contents = bubbles[0] if len(bubbles) == 1 else {"type": "carousel", "contents": bubbles}
    return {
        "type": "flex",
        "altText": title,
        "contents": contents,
        "quickReply": build_home_quick_reply(),
    }


def start_todo_create_mode(reply_token: str, user_id: str):
    set_user_mode(user_id, "todo_create")
    reply_messages(
        reply_token,
        [
            {
                "type": "flex",
                "altText": "To-do 建立",
                "contents": {
                    "type": "bubble",
                    "size": "mega",
                    "styles": build_bubble_styles(),
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "md",
                        "paddingAll": "20px",
                        "backgroundColor": CARD_BG,
                        "contents": [
                            build_card_header("To-do"),
                            {
                                "type": "text",
                                "text": "待辦模式已開啟",
                                "weight": "bold",
                                "size": "xl",
                                "color": CARD_INK,
                                "wrap": True,
                            },
                            {
                                "type": "text",
                                "text": "下一則訊息會建立待辦",
                                "size": "xs",
                                "color": CARD_MUTED,
                                "wrap": True,
                            },
                            {"type": "separator", "margin": "lg", "color": CARD_SUBTLE},
                            build_info_row("有效時間", "5 分鐘"),
                        ],
                    },
                    "footer": build_card_footer(
                        [
                            build_postback_button(
                                "取消",
                                "action=cancel_todo",
                                "取消待辦",
                                "secondary",
                            ),
                        ]
                    ),
                },
            },
        ],
    )


def start_todo_report_mode(reply_token: str, user_id: str, task_id: str):
    task = find_todo_task(task_id)
    if not task:
        reply_message(reply_token, "找不到這筆待辦，可能已被刪除或檔案已更新。")
        return
    set_user_mode(user_id, "todo_report", task_id=task_id)
    reply_messages(reply_token, [build_todo_report_mode_message(task)])


@app.get("/api/todos")
def get_todos(token: str = ""):
    verify_todo_dashboard_token(token)
    tasks = sorted(load_todo_tasks(), key=todo_dashboard_sort_key)
    return {
        "stats": build_todo_dashboard_stats(tasks),
        "tasks": [todo_task_to_api(task) for task in tasks],
    }


@app.post("/api/todos/{task_id}/status")
async def update_todo_status_from_dashboard(task_id: str, request: Request, token: str = ""):
    verify_todo_dashboard_token(token)
    payload = await request.json()
    status = str(payload.get("status", "")).strip()
    if status not in {"open", "done", "deleted"}:
        raise HTTPException(status_code=400, detail="status must be open, done, or deleted")
    task = update_todo_task_status(task_id, status)
    if not task:
        raise HTTPException(status_code=404, detail="Todo task not found")
    return {"task": todo_task_to_api(task), "stats": build_todo_dashboard_stats(load_todo_tasks())}


@app.post("/api/todos/{task_id}/report")
async def add_todo_report_from_dashboard(task_id: str, request: Request, token: str = ""):
    verify_todo_dashboard_token(token)
    payload = await request.json()
    report_text = str(payload.get("content", "")).strip()
    if not report_text:
        raise HTTPException(status_code=400, detail="report content is required")
    task = append_todo_report(task_id, report_text)
    if not task:
        raise HTTPException(status_code=404, detail="Todo task not found")
    return {"task": todo_task_to_api(task), "stats": build_todo_dashboard_stats(load_todo_tasks())}


@app.get("/todos", response_class=HTMLResponse)
def todos_dashboard(token: str = ""):
    verify_todo_dashboard_token(token)
    token_json = json.dumps(token)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>To-do Dashboard</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #f4f1ea;
      --ink: #20211d;
      --muted: #6f7068;
      --line: #d8d1c5;
      --panel: #fffdf7;
      --accent: #0f7b63;
      --accent-ink: #ffffff;
      --warn: #a24632;
      --done: #60736a;
      --shadow: rgba(69, 54, 35, 0.12);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", "Noto Sans TC", sans-serif;
      letter-spacing: 0;
      color: var(--ink);
      background:
        linear-gradient(90deg, rgba(32, 33, 29, 0.035) 1px, transparent 1px) 0 0 / 28px 28px,
        linear-gradient(rgba(32, 33, 29, 0.03) 1px, transparent 1px) 0 0 / 28px 28px,
        var(--paper);
    }}
    main {{
      width: min(1180px, calc(100vw - 28px));
      margin: 16px auto;
      padding: 18px 22px 22px;
      border: 1px solid var(--ink);
      border-radius: 10px;
      background: rgba(255, 253, 247, 0.72);
      box-shadow: 0 16px 34px rgba(69, 54, 35, 0.10);
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 18px;
      align-items: end;
      padding-bottom: 14px;
    }}
    .title-row {{
      display: flex;
      gap: 12px;
      align-items: center;
    }}
    .app-mark {{
      display: grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border: 2px solid var(--ink);
      border-radius: 5px;
      font-weight: 900;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(26px, 3vw, 42px);
      line-height: 1.08;
      font-family: Georgia, "Times New Roman", "Noto Serif TC", serif;
      font-weight: 700;
    }}
    .subtitle {{
      margin: 8px 0 0;
      max-width: 720px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.55;
    }}
    .sync {{
      text-align: right;
      color: var(--muted);
      font-size: 13px;
      white-space: nowrap;
    }}
    .sync strong {{
      color: var(--ink);
      font-weight: 600;
    }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(6, minmax(118px, 1fr));
      gap: 0;
      margin: 0 0 12px;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 253, 247, 0.88);
    }}
    .stat {{
      min-width: 0;
      padding: 12px 16px;
      border-right: 1px solid var(--line);
      background: transparent;
    }}
    .stat:last-child {{
      border-right: 0;
    }}
    .stat span {{
      display: flex;
      gap: 7px;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
    }}
    .stat-icon {{
      color: var(--ink);
      font-size: 14px;
    }}
    .stat strong {{
      display: block;
      margin-top: 6px;
      font-size: 24px;
      line-height: 1;
      font-family: Georgia, "Times New Roman", serif;
    }}
    .toolbar {{
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      margin: 0 0 12px;
      position: sticky;
      top: 0;
      z-index: 8;
      padding: 10px 0 8px;
      background:
        linear-gradient(90deg, rgba(32, 33, 29, 0.035) 1px, transparent 1px) 0 0 / 28px 28px,
        linear-gradient(rgba(32, 33, 29, 0.03) 1px, transparent 1px) 0 0 / 28px 28px,
        var(--paper);
    }}
    .filters {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    button {{
      min-height: 38px;
      border: 1px solid var(--ink);
      border-radius: 6px;
      padding: 8px 12px;
      background: var(--panel);
      color: var(--ink);
      font: inherit;
      font-size: 14px;
      cursor: pointer;
      transition: transform 140ms ease, background 140ms ease, color 140ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
    }}
    button.active {{
      background: var(--ink);
      color: var(--panel);
    }}
    .refresh {{
      background: var(--accent);
      border-color: var(--accent);
      color: var(--accent-ink);
      font-weight: 700;
    }}
    .danger {{
      background: #fff7f2;
      border-color: var(--warn);
      color: var(--warn);
      font-weight: 700;
    }}
    .workspace {{
      display: grid;
      grid-template-columns: minmax(340px, 0.9fr) minmax(420px, 1.1fr);
      gap: 0;
      height: calc(100vh - 126px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 253, 247, 0.70);
      overflow: hidden;
    }}
    .list-pane {{
      min-width: 0;
      border-right: 1px solid var(--line);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }}
    .list-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 14px 14px 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .task-list {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      padding: 0 10px 16px;
    }}
    .task-card {{
      display: grid;
      grid-template-columns: 44px minmax(0, 1fr);
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 253, 247, 0.92);
      box-shadow: none;
    }}
    .task-card.selected {{
      border-color: var(--accent);
      background: linear-gradient(90deg, rgba(15, 123, 99, 0.10), rgba(255, 253, 247, 0.94));
      box-shadow: 0 10px 24px rgba(15, 123, 99, 0.10);
    }}
    .task-card.done, .task-card.deleted {{
      color: var(--done);
      background: rgba(246, 244, 238, 0.86);
    }}
    .task-card.deleted {{
      color: #9b9186;
      text-decoration: line-through;
    }}
    .complete-button {{
      width: 24px;
      height: 24px;
      min-height: 24px;
      padding: 0;
      margin-top: 8px;
      border-color: #8d968e;
      border-radius: 3px;
      background: transparent;
      font-weight: 800;
      line-height: 1;
    }}
    .complete-button:disabled {{
      cursor: not-allowed;
      opacity: 0.55;
    }}
    .task-main {{
      min-width: 0;
    }}
    .task-topline, .task-meta, .detail-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
      align-items: center;
    }}
    .task-topline {{
      justify-content: space-between;
      margin-bottom: 8px;
    }}
    .task-id {{
      color: var(--muted);
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 12px;
    }}
    .card-title {{
      margin: 8px 0 4px;
      font-weight: 800;
      line-height: 1.45;
    }}
    .status-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      background: #fffaf0;
      color: var(--muted);
    }}
    .due {{
      font-weight: 700;
    }}
    .due.hot {{
      color: var(--warn);
    }}
    .due-chip, .project-chip {{
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      background: #fffaf0;
      color: var(--ink);
    }}
    .project-chip {{
      background: #f3f5ef;
    }}
    .content, .detail-content {{
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }}
    .content {{
      display: -webkit-box;
      overflow: hidden;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
      margin: 8px 0 10px;
    }}
    .select-task {{
      min-height: 32px;
      padding: 5px 10px;
      border-color: var(--line);
      color: var(--muted);
      background: transparent;
    }}
    .detail-panel {{
      position: sticky;
      top: 74px;
      align-self: start;
      height: 100%;
      max-height: none;
      min-height: 0;
      border: 0;
      border-radius: 0;
      background: var(--panel);
      box-shadow: none;
      overflow: hidden;
    }}
    .detail-shell {{
      display: flex;
      flex-direction: column;
      height: 100%;
      max-height: none;
      min-height: 0;
    }}
    .detail-head {{
      flex: 0 0 auto;
      padding: 18px 18px 12px;
      border-bottom: 1px solid var(--line);
      background: #f7f2e8;
    }}
    .detail-headline {{
      display: flex;
      align-items: start;
      justify-content: space-between;
      gap: 14px;
    }}
    .icon-button {{
      display: inline-grid;
      place-items: center;
      width: 34px;
      height: 34px;
      min-height: 34px;
      padding: 0;
      border-color: var(--line);
      background: transparent;
      font-size: 20px;
    }}
    .detail-title {{
      margin: 0;
      font-size: 20px;
      line-height: 1.4;
      font-weight: 800;
    }}
    .detail-body {{
      flex: 1 1 auto;
      min-height: 0;
      overflow: auto;
      padding: 16px 18px;
    }}
    .info-grid, .more-grid {{
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(255, 253, 247, 0.82);
    }}
    .more-grid {{
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .info-cell {{
      min-width: 0;
      padding: 9px 10px;
      border-right: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }}
    .info-cell:nth-child(5n), .more-grid .info-cell:nth-child(2n) {{
      border-right: 0;
    }}
    .info-cell span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 4px;
    }}
    .section-title {{
      margin: 18px 0 8px;
      font-size: 15px;
      font-weight: 800;
    }}
    .report-box {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 12px;
      background: #f8f6ef;
      line-height: 1.55;
    }}
    .detail-actions {{
      flex: 0 0 auto;
      display: flex;
      gap: 10px;
      justify-content: stretch;
      padding: 12px 18px;
      border-top: 1px solid var(--line);
      background: rgba(255, 253, 247, 0.96);
    }}
    .detail-actions button {{
      flex: 1 1 0;
    }}
    .detail-empty {{
      height: 100%;
      display: grid;
      place-items: center;
      padding: 24px;
      color: var(--muted);
      text-align: center;
    }}
    .mobile-detail {{
      display: none;
    }}
    .bottom-nav {{
      display: none;
    }}
    .report {{
      margin-top: 7px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    input[type="checkbox"] {{
      width: 22px;
      height: 22px;
      accent-color: var(--accent);
      cursor: pointer;
    }}
    .empty {{
      padding: 30px 16px;
      text-align: center;
      color: var(--muted);
    }}
    .status-line {{
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 760px) {{
      main {{
        width: 100vw;
        min-height: 100vh;
        margin: 0;
        padding: 14px 12px 74px;
        border: 0;
        border-radius: 0;
      }}
      header {{
        grid-template-columns: 1fr auto;
        align-items: center;
        gap: 10px;
      }}
      .sync {{
        grid-column: 1 / -1;
        text-align: center;
        white-space: normal;
      }}
      h1 {{
        font-size: 24px;
        font-family: "Segoe UI", "Noto Sans TC", sans-serif;
      }}
      .subtitle {{
        display: none;
      }}
      .stats {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
        display: grid;
        margin: 12px 0;
      }}
      .stat {{
        padding: 10px 12px;
        border-bottom: 1px solid var(--line);
      }}
      .stat:nth-child(3n) {{
        border-right: 0;
      }}
      .stat:nth-last-child(-n+3) {{
        border-bottom: 0;
      }}
      .stat strong {{
        font-size: 24px;
      }}
      .toolbar {{
        align-items: center;
        flex-direction: row;
        top: 0;
      }}
      .filters {{
        flex-wrap: nowrap;
        overflow-x: auto;
        padding-bottom: 2px;
      }}
      .filters button {{
        flex: 0 0 auto;
      }}
      .refresh {{
        width: auto;
      }}
      .workspace {{
        display: block;
        height: auto;
        min-height: 0;
        border: 0;
        background: transparent;
        overflow: visible;
      }}
      .list-pane {{
        border-right: 0;
      }}
      .list-head {{
        padding: 8px 0 10px;
      }}
      .task-list {{
        max-height: none;
        min-height: 0;
        overflow: visible;
        padding-right: 0;
      }}
      .task-card {{
        grid-template-columns: 44px minmax(0, 1fr);
        padding: 11px;
        margin-bottom: 10px;
      }}
      .detail-panel {{
        display: none;
        height: auto;
        max-height: none;
      }}
      .mobile-detail {{
        display: block;
        max-height: 320px;
        overflow: auto;
        margin-top: 10px;
        padding: 10px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: #fffaf0;
      }}
      .mobile-actions {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        margin-top: 10px;
      }}
      .bottom-nav {{
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        z-index: 12;
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        border-top: 1px solid var(--line);
        background: rgba(255, 253, 247, 0.96);
      }}
      .bottom-nav span {{
        display: grid;
        place-items: center;
        min-height: 52px;
        color: var(--muted);
        font-size: 12px;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <div class="title-row"><span class="app-mark">✓</span><h1>To-do Dashboard</h1></div>
        <p class="subtitle">主機 FastAPI 直接讀寫 vault 的 To-do 原始檔；筆電透過 ngrok 操作，狀態會回到 LINE Bot 共用的 `tasks.md`。</p>
      </div>
      <div class="sync">
        <div id="updated-at">尚未同步</div>
        <div><strong>來源：</strong>06_System/ToDo/tasks.md</div>
        <div id="status-line" class="status-line"></div>
      </div>
    </header>
    <section id="stats" class="stats" aria-label="待辦統計"></section>
    <div class="toolbar">
      <div class="filters" role="tablist" aria-label="待辦篩選">
        <button type="button" class="active" data-filter="open">未完成</button>
        <button type="button" data-filter="today">今日與逾期</button>
        <button type="button" data-filter="all">全部</button>
        <button type="button" data-filter="done">已完成</button>
        <button type="button" data-filter="deleted">已刪除</button>
      </div>
      <button type="button" id="refresh" class="refresh">重新整理</button>
    </div>
    <div class="workspace">
      <section class="list-pane" aria-label="待辦清單">
        <div class="list-head"><span id="list-count">共 0 筆待辦</span><span>依 期限 ↑ 更新時間 排序</span></div>
        <div class="task-list" id="task-list"></div>
      </section>
      <aside class="detail-panel" id="detail-panel" aria-label="待辦詳細內容"></aside>
      <div id="empty" class="empty" hidden>目前沒有符合條件的待辦。</div>
    </div>
    <nav class="bottom-nav" aria-label="手機導覽">
      <span>☷ 清單</span><span>□ 專案</span><span>☰ 回報</span><span>⚙ 設定</span>
    </nav>
  </main>
  <script>
    const token = {token_json};
    const state = {{
      filter: "open",
      selectedId: "",
      detailClosed: false,
      tasks: [],
      stats: {{}}
    }};

    const taskList = document.getElementById("task-list");
    const detailPanel = document.getElementById("detail-panel");
    const empty = document.getElementById("empty");
    const statusLine = document.getElementById("status-line");
    const updatedAt = document.getElementById("updated-at");
    const statsEl = document.getElementById("stats");
    const listCount = document.getElementById("list-count");

    const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (char) => ({{
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }}[char]));

    const setStatus = (text) => {{
      statusLine.textContent = text || "";
    }};

    const matchesFilter = (task) => {{
      if (state.filter === "all") return true;
      if (state.filter === "today") return task.status === "open" && task.is_due;
      return task.status === state.filter;
    }};

    const renderStats = () => {{
      const items = [
        ["☑", "未完成", state.stats.open || 0],
        ["↯", "今日 / 逾期", state.stats.today_or_overdue || 0],
        ["□", "有期限", state.stats.with_due || 0],
        ["☷", "無期限", state.stats.without_due || 0],
        ["✓", "已完成", state.stats.done || 0],
        ["⌫", "已刪除", state.stats.deleted || 0]
      ];
      statsEl.innerHTML = items.map(([icon, label, value]) => `
        <div class="stat"><span><b class="stat-icon">${{escapeHtml(icon)}}</b>${{escapeHtml(label)}}</span><strong>${{escapeHtml(value)}}</strong></div>
      `).join("");
    }};

    const getFilteredTasks = () => state.tasks.filter(matchesFilter);
    const firstLine = (value) => String(value || "").split("\\n").find((line) => line.trim()) || "未命名待辦";
    const compactDate = (value) => {{
      const text = String(value || "");
      const match = text.match(/(\\d{{4}})-(\\d{{2}})-(\\d{{2}})/);
      return match ? `${{match[2]}}/${{match[3]}}` : "無期限";
    }};
    const overdueText = (task) => task.is_due && task.status === "open" ? '<span class="due-chip due hot">逾期</span>' : "";

    const makePreview = (task) => {{
      const latest = task.latest_report ? `<div class="report">最近回報：${{escapeHtml(task.latest_report.content)}}</div>` : "";
      const disabled = task.status !== "open" ? "disabled" : "";
      const completeLabel = task.status === "done" ? "✓" : "";
      const selected = task.id === state.selectedId ? "selected" : "";
      const dueClass = task.is_due && task.status === "open" ? "due-chip due hot" : "due-chip due";
      const mobileDetail = task.id === state.selectedId ? `
        <div class="mobile-detail">
          <div class="detail-content">${{escapeHtml(task.content)}}</div>
          ${{latest}}
          <div class="mobile-actions">
            <button type="button" class="refresh" data-complete="${{escapeHtml(task.id)}}" ${{disabled}}>標記完成</button>
            <button type="button" data-report="${{escapeHtml(task.id)}}" ${{disabled}}>加入回報</button>
            <button type="button" data-copy="${{escapeHtml(task.id)}}">複製內容</button>
            <button type="button" class="danger" data-delete="${{escapeHtml(task.id)}}" ${{disabled}}>刪除</button>
          </div>
        </div>
      ` : "";
      return `
        <article class="task-card ${{escapeHtml(task.status)}} ${{selected}}" data-task-card="${{escapeHtml(task.id)}}">
          <button type="button" class="complete-button" data-complete="${{escapeHtml(task.id)}}" ${{disabled}} aria-label="完成 ${{escapeHtml(task.id)}}">${{completeLabel}}</button>
          <div class="task-main">
            <div class="task-topline">
              <div class="task-meta">
                <span class="${{dueClass}}">${{escapeHtml(compactDate(task.due))}}</span>
                ${{overdueText(task)}}
                <span class="project-chip">${{escapeHtml(task.project || "未分類")}}</span>
              </div>
              <span class="task-id">${{escapeHtml(task.id)}}</span>
            </div>
            <div class="card-title">${{escapeHtml(firstLine(task.content))}}</div>
            <div class="content">${{escapeHtml(task.content)}}</div>
            ${{latest}}
            <div class="task-topline">
              <span class="task-id">${{escapeHtml(task.id)}}</span>
              <span class="status-pill">${{escapeHtml(task.status_label)}}</span>
            </div>
            ${{mobileDetail}}
          </div>
        </article>
      `;
    }};

    const renderDetail = (task) => {{
      if (!task) {{
        detailPanel.innerHTML = '<div class="detail-empty">選一筆待辦，這裡會顯示完整內容。</div>';
        return;
      }}
      const disabled = task.status !== "open" ? "disabled" : "";
      const dueClass = task.is_due && task.status === "open" ? "due-chip due hot" : "due-chip due";
      const latestReport = task.latest_report ? `
        <div class="report-box">
          <div>${{escapeHtml(task.latest_report.created_at || "-")}}　${{escapeHtml(task.owner || "maintainer")}}</div>
          <div>${{escapeHtml(task.latest_report.content)}}</div>
        </div>
      ` : '<div class="report-box">尚無回報</div>';
      detailPanel.innerHTML = `
        <div class="detail-shell">
          <div class="detail-head">
            <div class="detail-headline">
              <div>
                <div class="detail-meta">
                  <span class="${{dueClass}}">${{escapeHtml(task.due || "無期限")}}</span>
                  ${{overdueText(task)}}
                  <span class="project-chip">${{escapeHtml(task.project || "未分類")}}</span>
                  <span class="status-pill">${{escapeHtml(task.status_label)}}</span>
                </div>
                <div class="detail-title">${{escapeHtml(firstLine(task.content))}}</div>
              </div>
              <button type="button" class="icon-button" data-close-detail aria-label="關閉詳細內容">×</button>
            </div>
            <div class="info-grid">
              <div class="info-cell"><span>專案</span>${{escapeHtml(task.project || "未分類")}}</div>
              <div class="info-cell"><span>類型</span>${{escapeHtml(task.type || "-")}}</div>
              <div class="info-cell"><span>負責人</span>${{escapeHtml(task.owner || "-")}}</div>
              <div class="info-cell"><span>期限</span>${{escapeHtml(task.due || "無期限")}}</div>
              <div class="info-cell"><span>來源</span>${{escapeHtml(task.source || "-")}}</div>
            </div>
          </div>
          <div class="detail-body">
            <div class="section-title">內容</div>
            <div class="detail-content">${{escapeHtml(task.content)}}</div>
            <div class="section-title">最新回報</div>
            ${{latestReport}}
            <div class="section-title">更多資訊</div>
            <div class="more-grid">
              <div class="info-cell"><span>建立時間</span>${{escapeHtml(task.created_at || "-")}}</div>
              <div class="info-cell"><span>更新時間</span>${{escapeHtml(task.updated_at || "-")}}</div>
              <div class="info-cell"><span>有無期限</span>${{task.due ? "有" : "無"}}</div>
              <div class="info-cell"><span>回報數量</span>${{escapeHtml((task.reports || []).length)}}</div>
            </div>
          </div>
          <div class="detail-actions">
            <button type="button" class="refresh" data-complete="${{escapeHtml(task.id)}}" ${{disabled}}>標記完成</button>
            <button type="button" data-report="${{escapeHtml(task.id)}}" ${{disabled}}>加入回報</button>
            <button type="button" data-copy="${{escapeHtml(task.id)}}">複製內容</button>
            <button type="button" class="danger" data-delete="${{escapeHtml(task.id)}}" ${{disabled}}>刪除</button>
          </div>
        </div>
      `;
    }};

    const renderTasks = () => {{
      const rows = getFilteredTasks();
      if (rows.length && !state.detailClosed && !rows.some((task) => task.id === state.selectedId)) {{
        state.selectedId = rows[0].id;
      }}
      if (!rows.length) {{
        state.selectedId = "";
        state.detailClosed = false;
      }}
      taskList.innerHTML = rows.map(makePreview).join("");
      listCount.textContent = `共 ${{rows.length}} 筆待辦`;
      renderDetail(state.detailClosed ? null : rows.find((task) => task.id === state.selectedId));
      empty.hidden = rows.length !== 0;
    }};

    const getTaskConfirmText = (taskId) => {{
      const task = state.tasks.find((item) => item.id === taskId);
      const content = task ? String(task.content || "").replace(/\\s+/g, " ").trim() : "";
      const preview = content.length > 48 ? content.slice(0, 47) + "..." : content;
      return `確定要將這筆待辦標記為完成？\\n\\n${{taskId}}${{preview ? "\\n" + preview : ""}}`;
    }};

    const getDeleteConfirmText = (taskId) => {{
      const task = state.tasks.find((item) => item.id === taskId);
      const content = task ? String(task.content || "").replace(/\\s+/g, " ").trim() : "";
      const preview = content.length > 48 ? content.slice(0, 47) + "..." : content;
      return `確定要刪除這筆待辦？\\n\\n${{taskId}}${{preview ? "\\n" + preview : ""}}`;
    }};

    const render = () => {{
      renderStats();
      renderTasks();
    }};

    const loadTodos = async () => {{
      setStatus("同步中...");
      const response = await fetch(`/api/todos?token=${{encodeURIComponent(token)}}`);
      if (!response.ok) {{
        setStatus("同步失敗");
        return;
      }}
      const data = await response.json();
      state.tasks = data.tasks || [];
      state.stats = data.stats || {{}};
      updatedAt.textContent = "同步時間 " + new Date().toLocaleString("zh-TW", {{hour12: false}});
      setStatus("");
      render();
    }};

    const setTaskStatus = async (taskId, status) => {{
      setStatus("寫入中...");
      const response = await fetch(`/api/todos/${{encodeURIComponent(taskId)}}/status?token=${{encodeURIComponent(token)}}`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{status}})
      }});
      if (!response.ok) {{
        setStatus("寫入失敗，已重新整理");
        await loadTodos();
        return;
      }}
      await loadTodos();
    }};

    const addTaskReport = async (taskId, content) => {{
      setStatus("寫入回報中...");
      const response = await fetch(`/api/todos/${{encodeURIComponent(taskId)}}/report?token=${{encodeURIComponent(token)}}`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{content}})
      }});
      if (!response.ok) {{
        setStatus("回報寫入失敗，已重新整理");
        await loadTodos();
        return;
      }}
      await loadTodos();
    }};

    const copyTaskContent = async (taskId) => {{
      const task = state.tasks.find((item) => item.id === taskId);
      if (!task) return;
      const text = `${{task.id}}\\n${{task.content || ""}}`;
      try {{
        await navigator.clipboard.writeText(text);
        setStatus("已複製內容");
      }} catch (error) {{
        setStatus("複製失敗，請改用手動選取");
      }}
    }};

    document.querySelectorAll("[data-filter]").forEach((button) => {{
      button.addEventListener("click", () => {{
        state.filter = button.dataset.filter;
        document.querySelectorAll("[data-filter]").forEach((item) => item.classList.toggle("active", item === button));
        renderTasks();
      }});
    }});

    const handleTaskAction = async (event) => {{
      const closeButton = event.target.closest("[data-close-detail]");
      if (closeButton) {{
        state.selectedId = "";
        state.detailClosed = true;
        renderTasks();
        return;
      }}
      const selectButton = event.target.closest("[data-select]");
      if (selectButton) {{
        state.selectedId = selectButton.dataset.select;
        state.detailClosed = false;
        renderTasks();
        return;
      }}
      const completeButton = event.target.closest("[data-complete]");
      if (completeButton) {{
        const taskId = completeButton.dataset.complete;
        if (!confirm(getTaskConfirmText(taskId))) return;
        completeButton.disabled = true;
        await setTaskStatus(taskId, "done");
        return;
      }}
      const reportButton = event.target.closest("[data-report]");
      if (reportButton) {{
        const taskId = reportButton.dataset.report;
        const content = prompt("輸入這筆待辦的回報內容");
        if (!content || !content.trim()) return;
        reportButton.disabled = true;
        await addTaskReport(taskId, content.trim());
        return;
      }}
      const copyButton = event.target.closest("[data-copy]");
      if (copyButton) {{
        await copyTaskContent(copyButton.dataset.copy);
        return;
      }}
      const deleteButton = event.target.closest("[data-delete]");
      if (deleteButton) {{
        const taskId = deleteButton.dataset.delete;
        if (!confirm(getDeleteConfirmText(taskId))) return;
        deleteButton.disabled = true;
        await setTaskStatus(taskId, "deleted");
        return;
      }}
      const card = event.target.closest("[data-task-card]");
      if (!card) return;
      state.selectedId = card.dataset.taskCard;
      state.detailClosed = false;
      renderTasks();
    }};

    taskList.addEventListener("click", handleTaskAction);
    detailPanel.addEventListener("click", handleTaskAction);

    document.getElementById("refresh").addEventListener("click", loadTodos);
    loadTodos();
  </script>
</body>
</html>"""
    )


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
                    "size": "mega",
                    "styles": build_bubble_styles(),
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "md",
                        "paddingAll": "20px",
                        "backgroundColor": CARD_BG,
                        "contents": [
                            build_card_header("Vault 回報"),
                            {
                                "type": "text",
                                "text": "回報模式已開啟",
                                "weight": "bold",
                                "size": "xl",
                                "color": CARD_INK,
                                "wrap": True,
                            },
                            {
                                "type": "text",
                                "text": "下一則訊息會記錄到 vault",
                                "size": "xs",
                                "color": CARD_MUTED,
                                "wrap": True,
                            },
                            {
                                "type": "separator",
                                "margin": "lg",
                                "color": CARD_SUBTLE,
                            },
                            build_info_row("有效時間", "5 分鐘"),
                        ],
                    },
                    "footer": build_card_footer(
                        [
                            build_postback_button(
                                "取消回報",
                                "action=cancel_report",
                                "取消回報",
                                "secondary",
                            ),
                        ]
                    ),
                },
            },
        ],
    )


def run_agent_and_push(user_id: str, prompt: str):
    start = time.time()
    log_debug(f"[AGENT START] user={user_id} backend={AGENT_BACKEND} prompt={prompt[:80]!r}")
    try:
        answer, answer_source = answer_query(prompt)
        log_debug(
            f"[AGENT DONE] user={user_id} elapsed={time.time() - start:.1f}s "
            f"source={answer_source} answer_len={len(answer or '')}"
        )
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
        answer, answer_source = answer_query(prompt)
        log_debug(
            f"[AGENT PAGE DONE] user={user_id} elapsed={time.time() - start:.1f}s "
            f"source={answer_source} answer_len={len(answer or '')}"
        )
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
    if action == "cancel_todo":
        clear_user_mode(user_id)
        reply_message(reply_token, "已取消待辦操作，回到一般查詢模式。")
        return
    if action == "direct_input":
        clear_user_mode(user_id)
        reply_message(reply_token, "已切換到直接輸入模式，請直接輸入查詢內容。")
        return
    if action == "daily_report":
        clear_user_mode(user_id)
        reply_messages(reply_token, [build_daily_report_message()])
        return
    if action == "todo_list_today":
        clear_user_mode(user_id)
        reply_messages(reply_token, [build_todo_list_message("today")])
        return
    if action == "todo_list_all":
        clear_user_mode(user_id)
        reply_messages(reply_token, [build_todo_list_message("all")])
        return
    if action == "todo_detail":
        clear_user_mode(user_id)
        task = find_todo_task(params.get("task_id", ""))
        if not task:
            reply_message(reply_token, "找不到這筆待辦，可能已被刪除或檔案已更新。")
            return
        reply_messages(reply_token, [build_todo_detail_message(task)])
        return
    if action == "todo_report":
        start_todo_report_mode(reply_token, user_id, params.get("task_id", ""))
        return
    if action == "todo_done":
        clear_user_mode(user_id)
        task = update_todo_task_status(params.get("task_id", ""), "done")
        if not task:
            reply_message(reply_token, "找不到這筆待辦，可能已被刪除或檔案已更新。")
            return
        reply_messages(reply_token, [build_todo_detail_message(task)])
        return
    if action == "todo_delete":
        clear_user_mode(user_id)
        task = update_todo_task_status(params.get("task_id", ""), "deleted")
        if not task:
            reply_message(reply_token, "找不到這筆待辦，可能已被刪除或檔案已更新。")
            return
        reply_messages(reply_token, [build_todo_detail_message(task)])
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

        if user_text in {"取消", "取消回報", "取消待辦"}:
            current_mode = USER_MODES.get(user_id, {}).get("mode", "")
            clear_user_mode(user_id)
            if current_mode in {"todo_create", "todo_report"}:
                reply_message(reply_token, "已取消待辦操作，回到一般查詢模式。")
            else:
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

        if user_text.lower() in {"to-do", "todo", "待辦"}:
            start_todo_create_mode(reply_token, user_id)
            continue

        if user_text == "今日待辦":
            clear_user_mode(user_id)
            reply_messages(reply_token, [build_todo_list_message("today")])
            continue

        if user_text == "全部待辦":
            clear_user_mode(user_id)
            reply_messages(reply_token, [build_todo_list_message("all")])
            continue

        if user_text == "直接輸入":
            clear_user_mode(user_id)
            reply_message(reply_token, "已切換到直接輸入模式，請直接輸入查詢內容。")
            continue

        if user_text in {"今日 Daily Report", "今日Daily Report", "查詢今日daily report", "查詢今日 Daily Report"}:
            clear_user_mode(user_id)
            reply_messages(reply_token, [build_daily_report_message()])
            continue

        direct_todo_match = re.match(r"^(待辦[:：]|todo[:：])\s*(.+)$", user_text, flags=re.I)
        if direct_todo_match:
            clear_user_mode(user_id)
            task = create_todo_task(direct_todo_match.group(2).strip())
            reply_messages(reply_token, [build_todo_created_message(task)])
            continue

        previous_mode = USER_MODES.get(user_id, {}).get("mode", "")
        had_mode = user_id in USER_MODES
        mode_state = get_user_mode_state(user_id)
        mode = str(mode_state.get("mode", ""))
        if had_mode and not mode:
            if previous_mode in {"todo_create", "todo_report"}:
                reply_message(reply_token, user_mode_timeout_text(previous_mode))
                continue
            page_path = make_answer_page("query", user_text)
            reply_messages(
                reply_token,
                [
                    {"type": "text", "text": user_mode_timeout_text(previous_mode)},
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

        if mode == "todo_create":
            clear_user_mode(user_id)
            task = create_todo_task(user_text)
            reply_messages(reply_token, [build_todo_created_message(task)])
            continue

        if mode == "todo_report":
            task_id = str(mode_state.get("task_id", ""))
            clear_user_mode(user_id)
            task = append_todo_report(task_id, user_text)
            if not task:
                reply_message(reply_token, "找不到這筆待辦，可能已被刪除或檔案已更新。")
                continue
            reply_messages(reply_token, [build_todo_detail_message(task)])
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
