import os
import hmac
import hashlib
import base64
import re
import threading
from urllib.parse import parse_qs

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException

from ask_claude import ask_claude
from ask_codex import ask_codex

load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
_raw = os.environ.get("LINE_ALLOWED_USER_IDS", os.environ.get("LINE_ALLOWED_USER_ID", ""))
ALLOWED_USER_IDS = {uid.strip() for uid in _raw.split(",") if uid.strip()}
AGENT_BACKEND = os.environ.get("AGENT_BACKEND", "claude").strip().lower()
VAULT_DIR = os.environ.get("VAULT_DIR", r"G:\MyDrive\my-vault")
PROJECTS_DIR = os.path.join(VAULT_DIR, "02_Projects")
PROJECTS_PER_PAGE = 8
USER_MODES: dict[str, str] = {}

app = FastAPI()


def verify_signature(body: bytes, signature: str) -> bool:
    hash_ = hmac.new(CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    expected = base64.b64encode(hash_).decode()
    return hmac.compare_digest(expected, signature)


def push_message(user_id: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"},
        json={"to": user_id, "messages": [{"type": "text", "text": text[:5000]}]},
    )


def reply_message(reply_token: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"},
        json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
    )


def reply_messages(reply_token: str, messages: list[dict]):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"},
        json={"replyToken": reply_token, "messages": messages},
    )


def ask_agent(prompt: str) -> str:
    if AGENT_BACKEND == "claude":
        return ask_claude(prompt)
    if AGENT_BACKEND == "codex":
        return ask_codex(prompt)
    return f"未知 AGENT_BACKEND={AGENT_BACKEND}，請設定為 claude 或 codex。"


def get_project_names() -> list[str]:
    if not os.path.isdir(PROJECTS_DIR):
        return []
    names = [
        entry.name
        for entry in os.scandir(PROJECTS_DIR)
        if entry.is_dir() and not entry.name.startswith(".")
    ]
    return sorted(names)


def parse_postback(data: str) -> tuple[str, dict[str, str]]:
    parsed = parse_qs(data, keep_blank_values=True)
    action = parsed.get("action", [""])[0]
    params = {key: values[0] for key, values in parsed.items() if values}
    return action, params


def build_home_text() -> str:
    return "請用下方 Rich Menu 操作，或直接輸入內容。"


def build_project_list_flex(page: int = 0) -> dict:
    projects = get_project_names()
    total = len(projects)
    start = page * PROJECTS_PER_PAGE
    end = start + PROJECTS_PER_PAGE
    page_items = projects[start:end]
    contents: list[dict] = [
        {
            "type": "text",
            "text": "專案摘要",
            "weight": "bold",
            "size": "xl",
        },
        {
            "type": "text",
            "text": f"第 {page + 1} 頁，共 {max((total - 1) // PROJECTS_PER_PAGE + 1, 1)} 頁",
            "size": "sm",
            "color": "#666666",
            "margin": "md",
        },
    ]

    if not page_items:
        contents.append(
            {
                "type": "text",
                "text": "目前找不到專案資料夾。",
                "size": "md",
                "margin": "lg",
                "wrap": True,
            }
        )
    else:
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
                                "data": f"action=project_summary&project={name}",
                                "displayText": f"查看專案：{name}",
                            },
                        },
                    ],
                }
            )

    footer_contents: list[dict] = []
    if start > 0:
        footer_contents.append(
            {
                "type": "button",
                "style": "secondary",
                "action": {
                    "type": "postback",
                    "label": "上一頁",
                    "data": f"action=query_projects&page={page - 1}",
                    "displayText": "查看上一頁專案",
                },
            }
        )
    if end < total:
        footer_contents.append(
            {
                "type": "button",
                "style": "primary",
                "action": {
                    "type": "postback",
                    "label": "下一頁",
                    "data": f"action=query_projects&page={page + 1}",
                    "displayText": "查看下一頁專案",
                },
            }
        )

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents,
        },
    }
    if footer_contents:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents,
        }
    return {
        "type": "flex",
        "altText": "專案清單",
        "contents": bubble,
    }


def build_project_summary(project_name: str) -> str:
    project_dir = os.path.join(PROJECTS_DIR, project_name)
    if not os.path.isdir(project_dir):
        return f"找不到專案：{project_name}"

    note_names = sorted(
        [
            name
            for name in os.listdir(project_dir)
            if name.lower().endswith(".md")
        ],
        reverse=True,
    )
    recent_notes = note_names[:3]

    lines = [
        f"專案：{project_name}",
        f"筆記數量：{len(note_names)}",
    ]
    if recent_notes:
        lines.append("最近筆記：")
        lines.extend([f"• {name[:-3]}" for name in recent_notes])
    else:
        lines.append("最近筆記：目前沒有 markdown 筆記")
    return "\n".join(lines)


def start_report_mode(reply_token: str, user_id: str):
    USER_MODES[user_id] = "report"
    reply_messages(
        reply_token,
        [
            {"type": "text", "text": "已切換到問題回報模式，請直接輸入內容。"},
            {"type": "text", "text": build_home_text()},
        ],
    )


def clear_user_mode(user_id: str):
    USER_MODES.pop(user_id, None)


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
                {"type": "text", "text": build_project_summary(project_name)},
                {"type": "text", "text": build_home_text()},
            ],
        )
        return
    reply_message(reply_token, build_home_text())


@app.post("/webhook")
async def webhook(request: Request):
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

        if USER_MODES.get(user_id) == "report":
            clear_user_mode(user_id)
            reply_message(reply_token, "已收到，正在整理並寫入 vault…")
            threading.Thread(
                target=run_report_and_push,
                args=(user_id, user_text),
                daemon=True,
            ).start()
            continue

        reply_message(reply_token, "思考中，請稍候…")
        threading.Thread(
            target=run_agent_and_push,
            args=(user_id, user_text),
            daemon=True,
        ).start()

    return "OK"
