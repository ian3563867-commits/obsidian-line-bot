import base64
import hashlib
import hmac
import json
import os
import tempfile
import time

from fastapi.testclient import TestClient

import main


CALLS = []


def fake_post(url, headers=None, json=None):
    CALLS.append({"url": url, "json": json})

    class Resp:
        status_code = 200

    return Resp()


def fake_ask_agent(prompt):
    if "問題回報內容" in prompt:
        return "已存到 00_Inbox/20260424-SampleProjectD問題回報.md"
    return f"AGENT:{prompt[:40]}"


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
    main.requests.post = fake_post
    main.ask_agent = fake_ask_agent
    main.USER_MODES.clear()
    main.ALLOWED_USER_IDS = set()

    temp_vault = tempfile.TemporaryDirectory()
    old_vault_dir = main.VAULT_DIR
    old_knowledge_dir = main.KNOWLEDGE_DIR
    old_daily_dir = main.DAILY_DIR
    old_obsidian_vault_name = main.OBSIDIAN_VAULT_NAME
    main.VAULT_DIR = temp_vault.name
    main.KNOWLEDGE_DIR = os.path.join(temp_vault.name, "04_Knowledge")
    main.DAILY_DIR = os.path.join(temp_vault.name, "03_Daily")
    main.OBSIDIAN_VAULT_NAME = "my-vault-test"
    os.makedirs(os.path.join(main.KNOWLEDGE_DIR, "0102-SampleProjectD"), exist_ok=True)
    os.makedirs(main.DAILY_DIR, exist_ok=True)
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

    client = TestClient(main.app)

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
    assert CALLS[-1]["json"]["messages"][0]["type"] == "flex"
    assert CALLS[-1]["json"]["messages"][0]["altText"] == "問題回報模式"

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
    assert "已收到，正在整理並寫入 vault" in CALLS[-2]["json"]["messages"][0]["text"]
    assert "已記錄到 vault" in CALLS[-1]["json"]["messages"][0]["text"]
    assert "00_Inbox\\20260424-SampleProjectD問題回報.md" in CALLS[-1]["json"]["messages"][0]["text"]

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
    assert "自動取消" in CALLS[-2]["json"]["messages"][0]["text"]
    assert CALLS[-1]["json"]["messages"][0]["text"].startswith("AGENT:")

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
    note_response = client.get(
        "/open-note",
        params={"file": "04_Knowledge/0102-SampleProjectD/20260424-SampleProjectD測試.md"},
    )
    assert note_response.status_code == 200
    assert "<h1" in note_response.text
    assert "<li>項目一</li>" in note_response.text
    assert "title: SampleProjectD測試" not in note_response.text

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
    daily_button = daily_msg["contents"]["footer"]["contents"][0]
    assert daily_button["action"]["uri"].startswith("http://testserver/open-note?")
    daily_response = client.get(
        "/open-note",
        params={"file": "03_Daily/20260424-daily-report.md"},
    )
    assert daily_response.status_code == 200
    assert "<table>" in daily_response.text

    print("OK")

    main.VAULT_DIR = old_vault_dir
    main.KNOWLEDGE_DIR = old_knowledge_dir
    main.DAILY_DIR = old_daily_dir
    main.OBSIDIAN_VAULT_NAME = old_obsidian_vault_name
    temp_vault.cleanup()


if __name__ == "__main__":
    run()
