import base64
import hashlib
import hmac
import json

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

    send_event(
        client,
        {
            "type": "message",
            "replyToken": "r2",
            "source": {"userId": "test-user"},
            "message": {"type": "text", "text": "回報問題"},
        },
    )
    assert main.USER_MODES.get("test-user") == "report"

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
            "type": "postback",
            "replyToken": "r4",
            "source": {"userId": "test-user"},
            "postback": {"data": "action=project_summary&project=0102-SampleProjectD"},
        },
    )
    assert CALLS[-1]["json"]["messages"][0]["text"].startswith("專案：0102-SampleProjectD")

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

    print("OK")


if __name__ == "__main__":
    run()
