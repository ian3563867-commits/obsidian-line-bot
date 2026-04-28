import os
import hmac
import hashlib
import base64
import re
import threading
import html
import time
from datetime import datetime
from urllib.parse import parse_qs, quote, urlencode

import markdown
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse

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
KNOWLEDGE_ITEMS_PER_PAGE = 5
KNOWLEDGE_NOTES_LIMIT = 5
FAST_PREVIEW_LIMIT = 3
REPORT_MODE_TIMEOUT_SECONDS = 5 * 60
USER_MODES: dict[str, dict] = {}
LAST_REQUEST_BASE_URL = ""

app = FastAPI()


def post_line(url: str, payload: dict):
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"},
        json=payload,
    )
    if response.status_code >= 400:
        print(f"[LINE API ERROR] status={response.status_code} body={response.text}")
    return response


def verify_signature(body: bytes, signature: str) -> bool:
    hash_ = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash_).decode()
    return hmac.compare_digest(expected, signature)


def push_message(user_id: str, text: str):
    post_line(
        "https://api.line.me/v2/bot/message/push",
        {"to": user_id, "messages": [{"type": "text", "text": text[:5000]}]},
    )


def reply_message(reply_token: str, text: str):
    post_line(
        "https://api.line.me/v2/bot/message/reply",
        {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
    )


def reply_messages(reply_token: str, messages: list[dict]):
    post_line(
        "https://api.line.me/v2/bot/message/reply",
        {"replyToken": reply_token, "messages": messages},
    )


def ask_agent(prompt: str) -> str:
    if AGENT_BACKEND == "claude":
        return ask_claude(prompt)
    if AGENT_BACKEND == "codex":
        return ask_codex(prompt)
    return f"未知 AGENT_BACKEND={AGENT_BACKEND}，請設定為 claude 或 codex。"


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


def get_recent_markdown_notes(folder: str, limit: int = KNOWLEDGE_NOTES_LIMIT) -> list[str]:
    if not os.path.isdir(folder):
        return []
    notes: list[tuple[float, str]] = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in files:
            if name.lower().endswith(".md"):
                path = os.path.join(root, name)
                notes.append((os.path.getmtime(path), path))
    return [path for _, path in sorted(notes, reverse=True)[:limit]]


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
    exp = int(time.time()) + OPEN_NOTE_TTL_SECONDS
    params = {
        "file": relative_path,
        "exp": str(exp),
        "sig": sign_open_note(relative_path, exp),
    }
    return base_url + "/open-note?" + urlencode(params, quote_via=quote)


def sign_open_note(file_path: str, exp: int) -> str:
    if not OPEN_NOTE_TOKEN:
        raise HTTPException(status_code=503, detail="OPEN_NOTE_TOKEN is not configured")
    message = f"{file_path}\n{exp}".encode("utf-8")
    return hmac.new(OPEN_NOTE_TOKEN.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_open_note_signature(file_path: str, exp: int, sig: str):
    if not OPEN_NOTE_TOKEN:
        raise HTTPException(status_code=503, detail="OPEN_NOTE_TOKEN is not configured")
    if exp < int(time.time()):
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


def render_markdown(content: str) -> str:
    return markdown.markdown(
        strip_frontmatter(content),
        extensions=[
            "extra",
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
    return "請用下方 Rich Menu 操作，或直接輸入內容。"


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
                    "contents": [
                        {
                            "type": "text",
                            "text": "Knowledge 摘要",
                            "weight": "bold",
                            "size": "xl",
                        },
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
    }


def build_project_page_bubble(page_items: list[str], page: int, total_pages: int) -> dict:
    contents: list[dict] = [
        {
            "type": "text",
            "text": "Knowledge 摘要",
            "weight": "bold",
            "size": "xl",
        },
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
            "contents": contents,
        },
    }


def build_project_summary(project_name: str) -> dict:
    knowledge_folder = os.path.join(KNOWLEDGE_DIR, project_name)
    if not os.path.isdir(knowledge_folder):
        return {"type": "text", "text": f"找不到 Knowledge：{project_name}"}

    recent_notes = get_recent_markdown_notes(knowledge_folder)
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
        "type": "flex",
        "altText": f"{project_name} Knowledge 筆記",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": contents,
            },
        },
    }


def build_daily_report_message() -> dict:
    today_path = get_today_daily_report_path()
    latest_path = get_latest_daily_report_path()
    if not today_path and not latest_path:
        return {"type": "text", "text": "目前找不到 Daily Report。"}

    target_path = today_path or latest_path
    title = os.path.splitext(os.path.basename(target_path))[0]
    status_text = "今日 Daily Report" if today_path else "今天尚未產生 Daily Report，先顯示最近一份。"
    return {
        "type": "flex",
        "altText": "Daily Report",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": "Daily Report",
                        "weight": "bold",
                        "size": "xl",
                    },
                    {
                        "type": "text",
                        "text": status_text,
                        "size": "sm",
                        "color": "#666666",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": title,
                        "size": "md",
                        "wrap": True,
                        "margin": "lg",
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "action": {
                            "type": "uri",
                            "label": "開啟報告",
                            "uri": build_note_open_url(target_path),
                        },
                    }
                ],
            },
        },
    }


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
      background: #111827;
      color: #f9fafb;
      border-radius: 8px;
      padding: 12px;
      overflow-x: auto;
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
                "altText": "問題回報模式",
                "contents": {
                    "type": "bubble",
                    "body": {
                        "type": "box",
                        "layout": "vertical",
                        "spacing": "md",
                        "contents": [
                            {
                                "type": "text",
                                "text": "問題回報模式",
                                "weight": "bold",
                                "size": "xl",
                                "wrap": True,
                            },
                            {
                                "type": "text",
                                "text": "下一則文字會被記錄到 vault",
                                "size": "sm",
                                "color": "#666666",
                                "wrap": True,
                            },
                            {
                                "type": "text",
                                "text": "請直接輸入要回報的問題內容。5 分鐘內未輸入會自動取消；若只是要查詢，請先取消回報。",
                                "size": "md",
                                "wrap": True,
                                "margin": "lg",
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
                                "style": "secondary",
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
    answer = ask_agent(prompt)
    push_message(user_id, answer)


def run_report_and_push(user_id: str, report_text: str):
    prompt = (
        "幫我新增到筆記，以下是問題回報內容。\n"
        "請沿用目前 vault 既有寫入規則與品質標準處理。\n\n"
        f"{report_text}"
    )
    answer = ask_agent(prompt)
    push_message(user_id, format_report_result(answer))


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
            reply_message(reply_token, build_home_text())
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
            reply_message(reply_token, "問題回報模式已超過 5 分鐘自動取消。這次改用一般查詢處理，思考中請稍候…")
            threading.Thread(
                target=run_agent_and_push,
                args=(user_id, user_text),
                daemon=True,
            ).start()
            continue

        if mode == "report":
            clear_user_mode(user_id)
            reply_message(reply_token, "已收到，正在整理並寫入 vault…")
            threading.Thread(
                target=run_report_and_push,
                args=(user_id, user_text),
                daemon=True,
            ).start()
            continue

        reply_message(reply_token, build_fast_preview(user_text))
        threading.Thread(
            target=run_agent_and_push,
            args=(user_id, user_text),
            daemon=True,
        ).start()

    return "OK"
