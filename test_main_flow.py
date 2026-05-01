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
        )
    with open(
        os.path.join(main.KNOWLEDGE_DIR, "0102-SampleProjectD", "20260424-SampleProjectD測試.md"),
        "w",
        encoding="utf-8",
    ) as f:
        f.write("---\ntitle: SampleProjectD測試\n---\n# SampleProjectD測試\n\n- 項目一\n")
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
        "查詢專案",
        "回報問題",
        "Daily Report",
    ]
    assert [
        item["action"]["label"]
        for item in menu_msg["contents"]["footer"]["contents"]
    ] == ["查詢專案", "回報問題", "Daily Report"]

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
    assert len(knowledge_contents["contents"]) == 4
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
    assert AGENT_CALLS[-1] == {"prompt": "SampleProjectD現場入庫異常", "allow_write": False}

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
        "查詢專案",
        "回報問題",
        "Daily Report",
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
    temp_vault.cleanup()


if __name__ == "__main__":
    run()
