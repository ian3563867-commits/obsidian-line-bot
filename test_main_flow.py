import base64
import hashlib
import hmac
import json
import os
import tempfile
import time
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


CALLS = []
AGENT_CALLS = []
SLOW_CONTEXT_AGENT = False


def fake_post(url, headers=None, json=None, **kwargs):
    CALLS.append({"url": url, "json": json})

    class Resp:
        status_code = 200

    return Resp()


def fake_ask_agent(prompt, allow_write=False):
    AGENT_CALLS.append({"prompt": prompt, "allow_write": allow_write})
    if SLOW_CONTEXT_AGENT and "建立第一輪討論背景包" in prompt:
        time.sleep(0.2)
    if "問題回報內容" in prompt:
        return "已存到 00_Inbox/20260424-SampleProjectD問題回報.md"
    return f"AGENT:{prompt[:40]}"


def text_messages():
    texts = []
    for call in CALLS:
        for message in call["json"].get("messages", []):
            if "text" in message:
                texts.append(message["text"])
    return texts


def wait_for_text(pattern, timeout=2):
    end = time.time() + timeout
    while time.time() < end:
        if any(pattern in text for text in text_messages()):
            return True
        time.sleep(0.01)
    return False


def wait_for_text_prefix(prefix, timeout=2):
    end = time.time() + timeout
    while time.time() < end:
        if any(text.startswith(prefix) for text in text_messages()):
            return True
        time.sleep(0.01)
    return False


def wait_for_agent_call_count(count, timeout=2):
    end = time.time() + timeout
    while time.time() < end:
        if len(AGENT_CALLS) >= count:
            return True
        time.sleep(0.01)
    return False


def wait_for_file_contains(root, pattern, timeout=2):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.isdir(root):
            for dirpath, _, filenames in os.walk(root):
                for filename in filenames:
                    if not filename.endswith(".md"):
                        continue
                    path = os.path.join(dirpath, filename)
                    with open(path, "r", encoding="utf-8") as f:
                        if pattern in f.read():
                            return True
        time.sleep(0.01)
    return False


def wait_for_discussion_status(client, discussion_id, status, timeout=2):
    end = time.time() + timeout
    last_payload = None
    while time.time() < end:
        response = client.get(f"/api/discussions/{discussion_id}")
        if response.status_code == 200:
            last_payload = response.json()
            if last_payload.get("status") == status:
                return last_payload
        time.sleep(0.01)
    return last_payload


def sign(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    digest = hmac.new(main.CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return body, base64.b64encode(digest).decode()


def send_event(client, event):
    body, signature = sign({"events": [event]})
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-Line-Signature": signature,
            "Content-Type": "application/json",
        },
    )


def run():
    global SLOW_CONTEXT_AGENT
    main.requests.post = fake_post
    main.ask_agent = fake_ask_agent
    main.USER_MODES.clear()
    main.ALLOWED_USER_IDS = set()
    AGENT_CALLS.clear()

    temp_vault = tempfile.TemporaryDirectory()
    old_vault_dir = main.VAULT_DIR
    old_knowledge_dir = main.KNOWLEDGE_DIR
    old_daily_dir = main.DAILY_DIR
    old_obsidian_vault_name = main.OBSIDIAN_VAULT_NAME
    old_open_note_token = main.OPEN_NOTE_TOKEN
    old_open_note_ttl_seconds = main.OPEN_NOTE_TTL_SECONDS
    old_open_note_result_ttl_seconds = main.OPEN_NOTE_RESULT_TTL_SECONDS
    old_answer_pages_dir = main.ANSWER_PAGES_DIR
    old_discussions_dir = main.DISCUSSIONS_DIR
    old_app_assets_dir = main.APP_ASSETS_DIR
    old_mind_palace_icon_path = main.MIND_PALACE_ICON_PATH
    old_todo_tasks_path = main.TODO_TASKS_PATH
    old_todo_dashboard_token = main.TODO_DASHBOARD_TOKEN
    main.VAULT_DIR = temp_vault.name
    main.KNOWLEDGE_DIR = os.path.join(temp_vault.name, "04_Knowledge")
    main.DAILY_DIR = os.path.join(temp_vault.name, "03_Daily")
    main.ANSWER_PAGES_DIR = os.path.join(temp_vault.name, "02_Projects", "9002-VaultLINEBot", "LineBotResults")
    main.OBSIDIAN_VAULT_NAME = "my-vault-test"
    main.OPEN_NOTE_TOKEN = "secret-token"
    main.OPEN_NOTE_TTL_SECONDS = 1800
    main.OPEN_NOTE_RESULT_TTL_SECONDS = 0
    main.APP_ASSETS_DIR = os.path.join(temp_vault.name, "assets")
    main.DISCUSSIONS_DIR = os.path.join(temp_vault.name, "02_Projects", "9002-VaultLINEBot", "WebDiscussionSessions")
    main.MIND_PALACE_ICON_PATH = os.path.join(main.APP_ASSETS_DIR, "mind-palace-icon.png")
    main.TODO_TASKS_PATH = ""
    main.TODO_DASHBOARD_TOKEN = "todo-dashboard-secret"
    os.makedirs(main.APP_ASSETS_DIR, exist_ok=True)
    with open(main.MIND_PALACE_ICON_PATH, "wb") as f:
        f.write(b"custom-icon")
    os.makedirs(os.path.join(main.KNOWLEDGE_DIR, "0102-SampleProjectD"), exist_ok=True)
    for idx in range(1, 18):
        os.makedirs(os.path.join(main.KNOWLEDGE_DIR, f"9002-測試{idx:02d}"), exist_ok=True)
    os.makedirs(main.DAILY_DIR, exist_ok=True)
    with open(os.path.join(main.KNOWLEDGE_DIR, "index.md"), "w", encoding="utf-8") as f:
        f.write(
            "| 頁面 | 摘要 | 標籤 |\n"
            "| --- | --- | --- |\n"
            "| [[0211-SampleProjectD-ASRS/20260409-問題紀錄]] | SampleProjectD ASRS 目前問題彙整 | ASRS, WMS, SampleProjectD |\n"
            "| [[0188-SampleProjectA/20260409-DN單刪除機制]] | SampleProjectA DN單刪除 | WMS, SampleProjectA |\n"
            "| [[生活/20260418-白沙屯媽祖遶境跟香]] | 生活紀錄 | life |\n"
            "| [[生活/20260423-家庭資產現況]] | 2026-04-23 資產快照：家庭合計 2,098,457 | life, 家庭, 資產, 財務 |\n"
            "| [[WMS通用/20260408-WMS常用SQL與API操作]] | 跨專案 WMS 常用 SQL；含 ALERTSYS 通知系統查詢（NS_M_SEND_GROUP、NS_M_USER） | WMS, SQL, API, 通用, ALERTSYS, 通知系統 |\n"
            "\n"
            "## 高價值操作原始檔（02_Projects 直接參考）\n"
            "\n"
            "| 原始檔案 | 說明 | 標籤 |\n"
            "| -------- | ---- | ---- |\n"
            "| [[02_Projects/0182-SampleProjectB/常用SQL]] | SampleProjectB WMS 常用 SQL / Message 入口；含關單、POClose/WOClose | WMS, SQL, Message, SampleProjectB |\n"
            "| [[02_Projects/0188-SampleProjectA/常用sql]] | SampleProjectA-Site WMS 常用 SQL / Message 入口；含關單 Message、調撥單 UI 不能上架 | WMS, SQL, Message, SampleProjectA |\n"
            "| [[04_Knowledge/WMS通用/20260408-WMS常用SQL與API操作]] | WMS 通用 SQL / API 操作入口；含 ALERTSYS 通知系統查詢（NS_M_SEND_GROUP、NS_M_USER） | WMS, SQL, API, 通用, ALERTSYS, 通知系統 |\n"
        )
    yuanzhan_sql = os.path.join(temp_vault.name, "02_Projects", "0182-SampleProjectB", "常用SQL.md")
    jianzhun_sql = os.path.join(temp_vault.name, "02_Projects", "0188-SampleProjectA", "常用sql.md")
    os.makedirs(os.path.dirname(yuanzhan_sql), exist_ok=True)
    os.makedirs(os.path.dirname(jianzhun_sql), exist_ok=True)
    wms_common_sql = os.path.join(main.KNOWLEDGE_DIR, "WMS通用", "20260408-WMS常用SQL與API操作.md")
    os.makedirs(os.path.dirname(wms_common_sql), exist_ok=True)
    with open(yuanzhan_sql, "w", encoding="utf-8") as f:
        f.write(
            "# SampleProjectB常用 SQL 與 Message\n\n"
            "## 查詢使用方式\n\n"
            "- `關單`、`POClose`、`WOClose`：優先看「關單 / 過帳異常」。\n\n"
            "## 完整目錄\n\n"
            "| 類別 | 段落 | 用途 | 常見查詢詞 |\n"
            "|---|---|---|---|\n"
            "| 關單 / 過帳異常 | [[#關單 / 過帳異常\\|關單 / 過帳異常]] | WO 關單、PO 關單 | WOClose、POClose、關單 |\n\n"
            "## 關單 / 過帳異常\n\n"
            "```json\n"
            "{\"EventID\":\"T5F1U22_POClose\"}\n"
            "```\n"
        )
    with open(jianzhun_sql, "w", encoding="utf-8") as f:
        f.write(
            "# SampleProjectA-Site常用 SQL 與 Message\n\n"
            "## 查詢使用方式\n\n"
            "- `MSG`、`Message`、`Message Test`：都指可貼到 WMS Message Test 的 JSON。\n\n"
            "## 完整目錄\n\n"
            "| 類別 | 段落 | 用途 | 常見查詢詞 |\n"
            "|---|---|---|---|\n"
            "| Message Test JSON | [[#關單 Message\\|關單 Message]] | WO 關單、PO 關單 | WOClose、POClose、關單、MSG |\n"
            "| 調撥 / 轉撥 / 移轉 | [[#調撥單 UI 不能上架原因 / 可上架明細\\|調撥單 UI 不能上架原因 / 可上架明細]] | 查可上架明細 | 調撥單UI、不能上架、可上架明細 |\n\n"
            "## 關單 Message\n\n"
            "```json\n"
            "{\"EventID\":\"T5F1U21_WOClose\"}\n"
            "```\n\n"
            "## 調撥單 UI 不能上架原因 / 可上架明細\n\n"
            "```sql\n"
            "SELECT * FROM WMS_T_ITEM_DEST;\n"
            "```\n"
        )
    with open(wms_common_sql, "w", encoding="utf-8") as f:
        f.write(
            "# WMS 常用 SQL 與 API 操作參考\n\n"
            "## ALERTSYS 通知系統查詢\n\n"
            "### 查詢發送群組與使用者列表\n\n"
            "```sql\n"
            "SELECT G.SEND_GROUP_NO, G.SEND_GROUP_DESC, STRING_AGG(U.USER_NAME, ', ')\n"
            "FROM NS_M_SEND_GROUP G\n"
            "LEFT JOIN NS_M_USER U ON G.SEND_GROUP_NO = U.SEND_GROUP_NO\n"
            "GROUP BY G.SEND_GROUP_NO, G.SEND_GROUP_DESC;\n"
            "```\n"
        )
    with open(
        os.path.join(main.KNOWLEDGE_DIR, "0102-SampleProjectD", "20260424-SampleProjectD測試.md"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write("---\ntitle: SampleProjectD測試\n---\n# SampleProjectD測試\n\n- 項目一\n")
    life_folder = os.path.join(main.KNOWLEDGE_DIR, "生活")
    os.makedirs(life_folder, exist_ok=True)
    with open(os.path.join(life_folder, "20260423-家庭資產現況.md"), "w", encoding="utf-8") as f:
        f.write(
            "# 家庭資產現況\n\n"
            "- 你：277,311\n"
            "- 名誼：1,821,146\n"
            "- 家庭合計：2,098,457\n"
        )
    with open(
        os.path.join(main.DAILY_DIR, "20260424-daily-report.md"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write("# Daily Report\n\n| 項目 | 狀態 |\n|---|---|\n| 測試 | OK |\n")

    with open(
        os.path.join(main.DAILY_DIR, "20260425-daily-report.md"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write("# Daily Report 2026-04-25\n\n| item | status |\n|---|---|\n| test | OK |\n")
    it_folder = os.path.join(main.KNOWLEDGE_DIR, "IT通用")
    os.makedirs(it_folder, exist_ok=True)
    for idx in range(1, 8):
        note_path = os.path.join(it_folder, f"2026042{idx}-IT通用測試{idx}.md")
        with open(note_path, "w", encoding="utf-8") as f:
            f.write(f"# IT通用測試{idx}\n")
        os.utime(note_path, (time.time() + idx, time.time() + idx))
    os.utime(
        os.path.join(main.DAILY_DIR, "20260425-daily-report.md"),
        (time.time() - 120, time.time() - 120),
    )
    os.utime(
        os.path.join(main.DAILY_DIR, "20260424-daily-report.md"),
        (time.time(), time.time()),
    )

    client = TestClient(main.app)

    answer, answer_source = main.answer_query("查詢SampleProjectA關單MSG")
    assert answer_source == "precheck"
    assert answer.startswith("AGENT:")
    assert len(AGENT_CALLS) == 1
    assert "Python deterministic index pre-check" in AGENT_CALLS[-1]["prompt"]
    assert "候選文件" in AGENT_CALLS[-1]["prompt"]
    assert "只在下方候選文件內" in AGENT_CALLS[-1]["prompt"]
    assert "02_Projects/0188-SampleProjectA/常用sql.md" in AGENT_CALLS[-1]["prompt"]
    assert "關單 Message" in AGENT_CALLS[-1]["prompt"]
    assert "WOClose" in AGENT_CALLS[-1]["prompt"]
    assert "T5F1U21_WOClose" not in AGENT_CALLS[-1]["prompt"]
    AGENT_CALLS.clear()

    answer, answer_source = main.answer_query("查詢關單msg")
    assert answer_source == "candidate_list"
    assert "多個可能入口" in answer
    assert "02_Projects/0182-SampleProjectB/常用SQL" in answer
    assert "02_Projects/0188-SampleProjectA/常用sql" in answer
    assert AGENT_CALLS == []

    answer, answer_source = main.answer_query("查詢通知系統sql，關鍵字還有ALERTSYS")
    assert answer_source == "precheck"
    assert answer.startswith("AGENT:")
    assert "04_Knowledge/WMS通用/20260408-WMS常用SQL與API操作.md" in AGENT_CALLS[-1]["prompt"]
    assert "NS_M_SEND_GROUP" in AGENT_CALLS[-1]["prompt"]
    AGENT_CALLS.clear()

    answer, answer_source = main.answer_query("查詢家庭資產")
    assert answer_source == "precheck"
    assert answer.startswith("AGENT:")
    assert "家庭資產現況" in AGENT_CALLS[-1]["prompt"]
    assert "生活/20260423-家庭資產現況.md" in AGENT_CALLS[-1]["prompt"]
    assert "家庭合計：2,098,457" not in AGENT_CALLS[-1]["prompt"]
    AGENT_CALLS.clear()

    answer, answer_source = main.answer_query("查詢SampleProjectD目前問題")
    assert answer_source == "index_hints"
    assert answer.startswith("AGENT:")
    assert AGENT_CALLS[-1]["allow_write"] is False
    assert "Index hints" in AGENT_CALLS[-1]["prompt"]
    assert "必須一併 Read 原始來源檔" in AGENT_CALLS[-1]["prompt"]
    assert "不可只靠 index hints 宣稱「所有」" in AGENT_CALLS[-1]["prompt"]
    assert "原始需求、解法流程、關鍵判斷、限制風險與後續事項" in AGENT_CALLS[-1]["prompt"]
    assert "0211-SampleProjectD-ASRS/20260409-問題紀錄" in AGENT_CALLS[-1]["prompt"]
    assert "查詢SampleProjectD目前問題" in AGENT_CALLS[-1]["prompt"]
    AGENT_CALLS.clear()

    discussions_home = client.get("/discussions")
    assert discussions_home.status_code == 200
    assert "Web Discussion Session" in discussions_home.text
    SLOW_CONTEXT_AGENT = True
    create_response = client.post(
        "/api/discussions",
        json={
            "title": "Pilot 與 Ray 窗口風險",
            "initial_input": "Pilot 把第一線窗口交給 Ray，這樣是否有風險？",
        },
    )
    assert create_response.status_code == 200
    discussion_id = create_response.json()["discussion_id"]
    queued_response = client.post(
        f"/api/discussions/{discussion_id}/messages",
        json={"content": "背景包還沒好時，我先補一個問題。"},
    )
    assert queued_response.status_code == 200
    assert queued_response.json()["queued"] is True
    assert wait_for_agent_call_count(2)
    SLOW_CONTEXT_AGENT = False
    discussion = wait_for_discussion_status(client, discussion_id, "active")
    assert discussion is not None
    assert discussion["title"] == "Pilot 與 Ray 窗口風險"
    assert discussion["status"] == "active"
    assert "AGENT:" in discussion["vault_context_summary"]
    assert len(discussion["messages"]) == 4
    assert discussion["messages"][1]["content"] == "背景包還沒好時，我先補一個問題。"
    assert discussion.get("pending_user_messages") == []
    discussion_page = client.get(f"/discussions/{discussion_id}")
    assert discussion_page.status_code == 200
    assert "Pilot 與 Ray 窗口風險" in discussion_page.text
    reply_response = client.post(
        f"/api/discussions/{discussion_id}/messages",
        json={"content": "那我該怎麼跟 Martin 說？"},
    )
    assert reply_response.status_code == 200
    assert wait_for_agent_call_count(3)
    discussion = wait_for_discussion_status(client, discussion_id, "active")
    assert discussion is not None
    assert discussion["status"] == "active"
    assert discussion["messages"][-2]["content"] == "那我該怎麼跟 Martin 說？"
    assert discussion["messages"][-1]["role"] == "assistant"
    assert "第一輪 vault 背景包" in AGENT_CALLS[-1]["prompt"]
    assert AGENT_CALLS[-1]["allow_write"] is False
    AGENT_CALLS.clear()

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r-menu",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "選單"},
        },
    )
    menu_msg = CALLS[-1]["json"]["messages"][0]
    assert menu_msg["type"] == "flex"
    assert menu_msg["altText"] == "功能選單"
    assert [item["action"]["label"] for item in menu_msg["quickReply"]["items"]] == [
        "To-do",
        "回報問題",
        "全部待辦",
    ]
    assert [
        item["action"]["label"]
        for item in menu_msg["contents"]["footer"]["contents"]
    ] == ["查詢專案", "回報問題", "Daily Report", "To-do", "今日待辦", "全部待辦"]

    todo_guard_start = len(AGENT_CALLS)
    call_guard_start = len(CALLS)
    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-start",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "To-do"},
        },
    )
    assert main.USER_MODES.get("test-user", {}).get("mode") == "todo_create"
    todo_start_msg = CALLS[-1]["json"]["messages"][0]
    assert todo_start_msg["altText"] == "To-do 建立"
    assert todo_start_msg["contents"]["body"]["contents"][1]["text"] == "待辦模式已開啟"

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-create",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "SampleProjectA小數防呆明天問 Mark"},
        },
    )
    assert "test-user" not in main.USER_MODES
    todo_path = main.get_todo_tasks_path()
    with open(todo_path, "r", encoding="utf-8") as f:
        todo_content = f.read()
    assert "T" in todo_content
    assert "## 待辦總覽" in todo_content
    assert "## 機器資料區" in todo_content
    assert "SampleProjectA小數防呆明天問 Mark" in todo_content
    assert "- status: open" in todo_content
    assert "- due:" in todo_content
    created_task = main.load_todo_tasks()[0]
    task_id = created_task["id"]
    assert created_task["project"] == "0188-SampleProjectA"
    assert CALLS[-1]["json"]["messages"][0]["altText"].startswith("待辦已建立")

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-all",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "全部待辦"},
        },
    )
    todo_all_msg = CALLS[-1]["json"]["messages"][0]
    assert todo_all_msg["altText"] == "全部待辦"
    assert task_id in str(todo_all_msg)

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-today",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "今日待辦"},
        },
    )
    assert "SampleProjectA小數防呆" not in str(CALLS[-1]["json"]["messages"][0])

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "todo-detail",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=todo_detail&task_id=" + task_id},
        },
    )
    assert CALLS[-1]["json"]["messages"][0]["altText"] == f"待辦 {task_id}"

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "todo-report-start",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=todo_report&task_id=" + task_id},
        },
    )
    assert main.USER_MODES.get("test-user", {}).get("mode") == "todo_report"
    assert main.USER_MODES["test-user"]["task_id"] == task_id
    todo_report_mode_msg = CALLS[-1]["json"]["messages"][0]
    assert todo_report_mode_msg["type"] == "flex"
    assert todo_report_mode_msg["altText"] == "待辦回報模式"
    assert todo_report_mode_msg["contents"]["body"]["contents"][1]["text"] == "請輸入最新進度"
    assert todo_report_mode_msg["contents"]["footer"]["contents"][0]["action"]["label"] == "取消"
    assert todo_report_mode_msg["contents"]["footer"]["contents"][0]["action"]["data"] == "action=cancel_todo"

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-report",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "Mark 說下午會看"},
        },
    )
    assert "Mark 說下午會看" in str(CALLS[-1]["json"]["messages"][0])
    assert "Mark 說下午會看" in main.load_todo_tasks()[0]["reports"][0]["content"]

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "todo-done",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=todo_done&task_id=" + task_id},
        },
    )
    assert main.find_todo_task(task_id)["status"] == "done"
    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-all-empty",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "全部待辦"},
        },
    )
    assert "目前沒有未完成待辦" in CALLS[-1]["json"]["messages"][0]["text"]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-direct-create",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "待辦：今天整理 LINE Bot To-do 測試"},
        },
    )
    second_task_id = main.load_todo_tasks()[-1]["id"]
    bad_todos_response = client.get("/api/todos?token=bad-token")
    assert bad_todos_response.status_code == 403
    todos_response = client.get("/api/todos?token=todo-dashboard-secret")
    assert todos_response.status_code == 200
    todos_data = todos_response.json()
    assert todos_data["stats"]["open"] == 1
    assert any(task["id"] == second_task_id for task in todos_data["tasks"])
    dashboard_response = client.get("/todos?token=todo-dashboard-secret")
    assert dashboard_response.status_code == 200
    assert "To-do Dashboard" in dashboard_response.text
    assert "確定要將這筆待辦標記為完成" in dashboard_response.text
    assert "max-height: calc(100vh - 280px)" in dashboard_response.text
    assert "overflow: auto" in dashboard_response.text
    assert "position: sticky" in dashboard_response.text
    invalid_status_response = client.post(
        f"/api/todos/{second_task_id}/status?token=todo-dashboard-secret",
        json={"status": "waiting"},
    )
    assert invalid_status_response.status_code == 400
    missing_status_response = client.post(
        "/api/todos/T19990101-404/status?token=todo-dashboard-secret",
        json={"status": "done"},
    )
    assert missing_status_response.status_code == 404
    done_from_dashboard = client.post(
        f"/api/todos/{second_task_id}/status?token=todo-dashboard-secret",
        json={"status": "done"},
    )
    assert done_from_dashboard.status_code == 200
    assert main.find_todo_task(second_task_id)["status"] == "done"
    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "todo-delete",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=todo_delete&task_id=" + second_task_id},
        },
    )
    assert main.find_todo_task(second_task_id)["status"] == "deleted"
    with open(todo_path, "r", encoding="utf-8") as f:
        assert second_task_id in f.read()

    assert main.parse_todo_due("本週整理規格") == (datetime.now().date() + main.timedelta(days=6 - datetime.now().date().weekday())).isoformat()
    assert main.parse_todo_due("下週提醒 Mark") == (datetime.now().date() + main.timedelta(days=13 - datetime.now().date().weekday())).isoformat()
    assert main.parse_todo_due("5/30 前確認") != ""
    assert main.parse_todo_due("2026/06/01 前確認") == "2026-06-01"

    bulk_tasks = main.load_todo_tasks()
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now().date() - main.timedelta(days=1)).isoformat()
    for idx in range(20):
        bulk_tasks.append(
            {
                "id": f"T20990101-{idx + 1:03d}",
                "status": "open",
                "type": "work",
                "project": "9002-VaultLINEBot",
                "content": f"批次測試待辦 {idx + 1}",
                "owner": "maintainer",
                "due": today if idx % 2 == 0 else "",
                "created_at": f"2099-01-01 00:{idx:02d}:00",
                "updated_at": f"2099-01-01 00:{idx:02d}:00",
                "source": "line_bot",
                "reports": [],
            }
        )
    bulk_tasks.append(
        {
            "id": "T20990101-999",
            "status": "open",
            "type": "work",
            "project": "9002-VaultLINEBot",
            "content": "逾期待辦排序測試",
            "owner": "maintainer",
            "due": yesterday,
            "created_at": "2099-01-01 00:59:00",
            "updated_at": "2099-01-01 00:59:00",
            "source": "line_bot",
            "reports": [],
        }
    )
    main.save_todo_tasks(bulk_tasks)

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-carousel",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "全部待辦"},
        },
    )
    carousel_msg = CALLS[-1]["json"]["messages"][0]
    assert carousel_msg["contents"]["type"] == "carousel"
    assert len(carousel_msg["contents"]["contents"]) == 5
    for bubble in carousel_msg["contents"]["contents"]:
        task_boxes = [
            item
            for item in bubble["body"]["contents"]
            if item.get("type") == "box" and item.get("layout") == "vertical"
        ]
        assert len(task_boxes) <= main.TODO_ITEMS_PER_BUBBLE

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-today-list",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "今日待辦"},
        },
    )
    today_msg = CALLS[-1]["json"]["messages"][0]
    assert "逾期待辦排序測試" in str(today_msg)
    assert "批次測試待辦 1" in str(today_msg)
    assert "批次測試待辦 2" not in str(today_msg)

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "todo-missing-detail",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=todo_detail&task_id=T19990101-404"},
        },
    )
    assert "找不到這筆待辦" in CALLS[-1]["json"]["messages"][0]["text"]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-cancel-start",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "To-do"},
        },
    )
    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-cancel",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "取消"},
        },
    )
    assert "已取消待辦操作" in CALLS[-1]["json"]["messages"][0]["text"]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-timeout-start",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "To-do"},
        },
    )
    main.USER_MODES["test-user"]["started_at"] = time.time() - main.REPORT_MODE_TIMEOUT_SECONDS - 1
    send_event(
        client,
        {
            "type": "message",
            "replyToken": "todo-timeout",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "這筆不應該建立"},
        },
    )
    assert "待辦操作已超過 5 分鐘自動取消" in CALLS[-1]["json"]["messages"][0]["text"]
    assert "這筆不應該建立" not in open(todo_path, "r", encoding="utf-8").read()
    todo_calls = CALLS[call_guard_start:]
    assert len(AGENT_CALLS) == todo_guard_start
    assert all("/push" not in call["url"] for call in todo_calls)
    assert not os.path.isdir(main.ANSWER_PAGES_DIR)

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r1",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "查詢專案"},
        },
    )
    assert CALLS[-1]["json"]["messages"][1]["type"] == "flex"
    assert CALLS[-1]["json"]["messages"][1]["altText"] == "Knowledge 清單"
    knowledge_contents = CALLS[-1]["json"]["messages"][1]["contents"]
    assert knowledge_contents["type"] == "carousel"
    assert len(knowledge_contents["contents"]) == 5
    for bubble in knowledge_contents["contents"]:
        assert bubble["body"]["contents"][0]["contents"][0]["text"] == "Knowledge 摘要"
        assert "/mind-palace-icon.png?v=" in bubble["body"]["contents"][0]["contents"][1]["url"]
        project_boxes = [
            item
            for item in bubble["body"]["contents"]
            if item.get("type") == "box" and item.get("layout") == "vertical"
        ]
        assert len(project_boxes) <= main.KNOWLEDGE_ITEMS_PER_PAGE

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r2",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "回報問題"},
        },
    )
    assert main.USER_MODES.get("test-user", {}).get("mode") == "report"
    report_mode_msg = CALLS[-1]["json"]["messages"][0]
    assert report_mode_msg["type"] == "flex"
    assert report_mode_msg["altText"] == "Vault 回報"
    report_mode_body = report_mode_msg["contents"]["body"]["contents"]
    assert report_mode_body[0]["contents"][0]["text"] == "Vault 回報"
    assert "/mind-palace-icon.png?v=" in report_mode_body[0]["contents"][1]["url"]
    assert report_mode_body[1]["text"] == "回報模式已開啟"
    assert report_mode_body[2]["text"] == "下一則訊息會記錄到 vault"
    assert report_mode_body[4]["contents"][0]["text"] == "有效時間"
    assert report_mode_body[4]["contents"][1]["text"] == "5 分鐘"

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "r2-cancel",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=cancel_report"},
        },
    )
    assert "test-user" not in main.USER_MODES
    assert "已取消問題回報" in CALLS[-1]["json"]["messages"][0]["text"]

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "r2-direct",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=direct_input"},
        },
    )
    assert "已切換到直接輸入模式" in CALLS[-1]["json"]["messages"][0]["text"]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r2-direct-query",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "SampleProjectD現場入庫異常"},
        },
    )
    assert CALLS[-1]["json"]["messages"][0]["altText"] == "Vault 查詢"
    assert wait_for_agent_call_count(1)
    assert AGENT_CALLS[-1]["allow_write"] is False
    assert "Index hints" in AGENT_CALLS[-1]["prompt"]
    assert "SampleProjectD現場入庫異常" in AGENT_CALLS[-1]["prompt"]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r2-direct-report",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "紀錄：SampleProjectD現場入庫異常"},
        },
    )
    assert CALLS[-1]["json"]["messages"][0]["altText"] == "Vault 回報"
    assert wait_for_agent_call_count(2)
    assert AGENT_CALLS[-1]["prompt"].endswith("SampleProjectD現場入庫異常")
    assert AGENT_CALLS[-1]["allow_write"] is True

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r2b",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "回報問題"},
        },
    )
    assert main.USER_MODES.get("test-user", {}).get("mode") == "report"

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r3",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "SampleProjectD現場入庫異常"},
        },
    )
    assert CALLS[-1]["json"]["messages"][0]["altText"] == "Vault 回報"
    assert wait_for_file_contains(main.ANSWER_PAGES_DIR, "已記錄到 vault")
    assert wait_for_file_contains(main.ANSWER_PAGES_DIR, "00_Inbox\\20260424-SampleProjectD問題回報.md")
    assert [item["action"]["label"] for item in CALLS[-1]["json"]["messages"][0]["quickReply"]["items"]] == [
        "To-do",
        "回報問題",
        "全部待辦",
    ]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r3-mode",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "回報問題"},
        },
    )
    main.USER_MODES["test-user"]["started_at"] = time.time() - main.REPORT_MODE_TIMEOUT_SECONDS - 1
    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r3-timeout",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "查一下SampleProjectD狀態"},
        },
    )
    assert wait_for_text("自動取消")
    assert wait_for_file_contains(main.ANSWER_PAGES_DIR, "AGENT:")

    send_event(
        client,
        {
            "type": "postback",
            "replyToken": "r4",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=project_summary&project=0102-SampleProjectD"},
        },
    )
    summary = CALLS[-1]["json"]["messages"][0]
    assert summary["type"] == "flex"
    assert summary["contents"]["body"]["contents"][0]["text"] == "0102-SampleProjectD"
    note_button = summary["contents"]["body"]["contents"][2]
    assert note_button["action"]["type"] == "uri"
    assert note_button["action"]["uri"].startswith("http://testserver/open-note?")
    assert "exp=" in note_button["action"]["uri"]
    assert "sig=" in note_button["action"]["uri"]
    assert "token=secret-token" not in note_button["action"]["uri"]
    it_summary = main.build_project_summary("IT通用")
    assert it_summary["contents"]["type"] == "carousel"
    assert len(it_summary["contents"]["contents"]) == 2
    it_note_buttons = [
        item
        for bubble in it_summary["contents"]["contents"]
        for item in bubble["body"]["contents"]
        if item.get("type") == "button"
    ]
    assert len(it_note_buttons) == 7
    denied_response = client.get(
        "/open-note",
        params={"file": "04_Knowledge/0102-SampleProjectD/20260424-SampleProjectD測試.md"},
    )
    assert denied_response.status_code == 403
    signed_file = "04_Knowledge/0102-SampleProjectD/20260424-SampleProjectD測試.md"
    signed_exp = int(time.time()) + 60
    signed_sig = main.sign_open_note(signed_file, signed_exp)
    note_response = client.get(
        "/open-note",
        params={"file": signed_file, "exp": signed_exp, "sig": signed_sig},
    )
    assert note_response.status_code == 200
    assert "<h1" in note_response.text
    assert "<li>項目一</li>" in note_response.text
    assert "title: SampleProjectD測試" not in note_response.text
    tampered_response = client.get(
        "/open-note",
        params={"file": "03_Daily/20260424-daily-report.md", "exp": signed_exp, "sig": signed_sig},
    )
    assert tampered_response.status_code == 403
    expired_file = "04_Knowledge/0102-SampleProjectD/20260424-SampleProjectD測試.md"
    expired_exp = int(time.time()) - 1
    expired_response = client.get(
        "/open-note",
        params={"file": expired_file, "exp": expired_exp, "sig": main.sign_open_note(expired_file, expired_exp)},
    )
    assert expired_response.status_code == 403
    result_page_path = os.path.join(main.ANSWER_PAGES_DIR, "20260424-120000-query-12345.md")
    os.makedirs(os.path.dirname(result_page_path), exist_ok=True)
    with open(result_page_path, "w", encoding="utf-8") as f:
        f.write("# result page\n\n{\n  \"EventID\": \"TEST\"\n}\n")
    non_expiring_url = main.build_note_open_url(result_page_path)
    assert "exp=0" in non_expiring_url
    non_result_url = main.build_note_open_url(os.path.join(main.VAULT_DIR, signed_file))
    assert "exp=0" not in non_result_url
    non_expiring_response = client.get(
        "/open-note",
        params={
            "file": os.path.relpath(result_page_path, main.VAULT_DIR).replace("\\", "/"),
            "exp": 0,
            "sig": main.sign_open_note(os.path.relpath(result_page_path, main.VAULT_DIR).replace("\\", "/"), 0),
        },
    )
    assert non_expiring_response.status_code == 200
    assert "<pre><code" in non_expiring_response.text
    assert "copy-code" in non_expiring_response.text
    assert "navigator.clipboard" in non_expiring_response.text

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r5",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "直接輸入"},
        },
    )
    assert "已切換到直接輸入模式" in CALLS[-1]["json"]["messages"][0]["text"]

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r6",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "今日 Daily Report"},
        },
    )
    daily_msg = CALLS[-1]["json"]["messages"][0]
    assert daily_msg["type"] == "flex"
    daily_body = daily_msg["contents"]["body"]["contents"]
    assert daily_body[0]["contents"][0]["text"] == "Daily Report"
    assert "/mind-palace-icon.png?v=" in daily_body[0]["contents"][1]["url"]
    assert daily_body[1]["text"] == "顯示最近一份報告"
    assert daily_body[2]["text"] == "20260425-daily-report"
    assert daily_body[4]["contents"][0]["text"] == "原因"
    assert daily_body[4]["contents"][1]["text"] == "今日尚未產生"
    daily_button = daily_msg["contents"]["footer"]["contents"][0]
    assert daily_button["action"]["label"] == "開啟"
    assert daily_button["action"]["uri"].startswith("http://testserver/open-note?")
    assert "sig=" in daily_button["action"]["uri"]
    daily_file = "03_Daily/20260425-daily-report.md"
    daily_exp = int(time.time()) + 60
    daily_response = client.get(
        "/open-note",
        params={"file": daily_file, "exp": daily_exp, "sig": main.sign_open_note(daily_file, daily_exp)},
    )
    assert daily_response.status_code == 200
    assert "<table>" in daily_response.text

    today_report = os.path.join(main.DAILY_DIR, datetime.now().strftime("%Y%m%d-daily-report.md"))
    with open(today_report, "w", encoding="utf-8") as f:
        f.write("# Daily Report Today\n\n| item | status |\n|---|---|\n| today | OK |\n")
    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r6-today",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "今日 Daily Report"},
        },
    )
    today_daily_msg = CALLS[-1]["json"]["messages"][0]
    today_daily_body = today_daily_msg["contents"]["body"]["contents"]
    assert today_daily_body[1]["text"] == "今日報告已就緒"
    assert today_daily_body[2]["text"] == os.path.splitext(os.path.basename(today_report))[0]
    assert today_daily_body[4]["contents"][0]["text"] == "來源"
    assert today_daily_body[4]["contents"][1]["text"] == "今日 Daily Report"

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r7",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "SampleProjectD ASRS 目前問題"},
        },
    )
    answer_msg = CALLS[-1]["json"]["messages"][0]
    assert answer_msg["altText"] == "Vault 查詢"
    answer_body = answer_msg["contents"]["body"]["contents"]
    assert answer_body[0]["contents"][0]["text"] == "Vault 查詢"
    assert "/mind-palace-icon.png?v=" in answer_body[0]["contents"][1]["url"]
    assert answer_body[1]["text"] == "結果頁已建立"
    assert answer_body[2]["text"].endswith(" 建立")
    assert answer_body[4]["contents"][0]["text"] == "查詢內容"
    assert answer_body[4]["contents"][1]["text"] == "SampleProjectD ASRS 目前問題"
    assert answer_msg["contents"]["footer"]["contents"][0]["action"]["label"] == "開啟"
    with patch("main.build_mind_palace_icon_png", side_effect=AssertionError("should read local icon")):
        icon_response = client.get("/mind-palace-icon.png")
    assert icon_response.status_code == 200
    assert icon_response.content == b"custom-icon"
    assert wait_for_file_contains(main.ANSWER_PAGES_DIR, "SampleProjectD ASRS 目前問題")
    assert wait_for_file_contains(main.ANSWER_PAGES_DIR, "AGENT:")

    print("OK")

    main.VAULT_DIR = old_vault_dir
    main.KNOWLEDGE_DIR = old_knowledge_dir
    main.DAILY_DIR = old_daily_dir
    main.OBSIDIAN_VAULT_NAME = old_obsidian_vault_name
    main.OPEN_NOTE_TOKEN = old_open_note_token
    main.OPEN_NOTE_TTL_SECONDS = old_open_note_ttl_seconds
    main.OPEN_NOTE_RESULT_TTL_SECONDS = old_open_note_result_ttl_seconds
    main.ANSWER_PAGES_DIR = old_answer_pages_dir
    main.DISCUSSIONS_DIR = old_discussions_dir
    main.APP_ASSETS_DIR = old_app_assets_dir
    main.MIND_PALACE_ICON_PATH = old_mind_palace_icon_path
    main.TODO_TASKS_PATH = old_todo_tasks_path
    main.TODO_DASHBOARD_TOKEN = old_todo_dashboard_token
    temp_vault.cleanup()


if __name__ == "__main__":
    run()
