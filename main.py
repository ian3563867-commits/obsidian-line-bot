import os
import hmac
import hashlib
import base64
import threading
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException

from ask_claude import ask_claude

load_dotenv()

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ALLOWED_USER_ID = os.environ.get("LINE_ALLOWED_USER_ID", "")

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


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()

    if not verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    data = await request.json() if not body else __import__("json").loads(body)

    for event in data.get("events", []):
        if event.get("type") != "message":
            continue
        if event.get("message", {}).get("type") != "text":
            continue

        user_id = event["source"]["userId"]
        user_text = event["message"]["text"].strip()
        reply_token = event["replyToken"]

        if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
            continue

        reply_message(reply_token, "思考中，請稍候…")

        def run_claude(uid=user_id, prompt=user_text):
            answer = ask_claude(prompt)
            push_message(uid, answer)

        threading.Thread(target=run_claude, daemon=True).start()

    return "OK"
