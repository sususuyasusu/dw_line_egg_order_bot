"""
LINE Bot: どら山社員グループの「卵発注」メッセージを
製造表 Sheets の確定便 (AU/AV) 列に反映する。
"""
import logging
import os
import traceback

from fastapi import FastAPI, HTTPException, Request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    GroupSource,
    MessageEvent,
    TextMessageContent,
)

from parser import format_reply, parse, starts_with_egg_order
from sheets import get_client_from_env

CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ALLOWED_GROUP_IDS = {
    g.strip() for g in os.environ.get("ALLOWED_GROUP_IDS", "").split(",") if g.strip()
}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("egg-order-bot")

handler = WebhookHandler(CHANNEL_SECRET)
configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok", "service": "dw_line_egg_order_bot"}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}


def _reply(reply_token: str, text: str) -> None:
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(
            ReplyMessageRequest(
                reply_token=reply_token,
                messages=[TextMessage(text=text)],
            )
        )


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    text = event.message.text or ""

    # 「卵発注」で始まる本文のみ処理
    if not starts_with_egg_order(text):
        return

    # グループ ID 制限（ALLOWED_GROUP_IDS が空なら全許可）
    src = event.source
    if ALLOWED_GROUP_IDS:
        gid = getattr(src, "group_id", None) if isinstance(src, GroupSource) else None
        if gid not in ALLOWED_GROUP_IDS:
            log.info("ignored: group_id=%s not in allowed list", gid)
            return

    try:
        items = parse(text)
    except Exception as e:
        log.exception("parse failed")
        _reply(event.reply_token, f"⚠️ パース失敗: {e}")
        return

    if not items:
        _reply(
            event.reply_token,
            "⚠️ 発注内容を読み取れませんでした。\n"
            "フォーマット:\n卵発注\n・2日（火）\n　卵黄 5kg×2\n　卵白 5kg×6",
        )
        return

    try:
        client = get_client_from_env()
        results = client.write_orders(items)
    except Exception as e:
        log.exception("sheets write failed")
        tb = traceback.format_exc()
        log.error(tb)
        _reply(event.reply_token, f"⚠️ シート書込失敗: {e}")
        return

    ok_items = [r.item for r in results if r.ok]
    ng_lines = [
        f"× {r.item.date.month}/{r.item.date.day}: {r.error}"
        for r in results
        if not r.ok
    ]

    note = ""
    if ng_lines:
        note = "未反映:\n" + "\n".join(ng_lines)

    reply_text = format_reply(ok_items, note=note) if ok_items else (
        "⚠️ すべての日付で書込失敗\n" + "\n".join(ng_lines)
    )
    _reply(event.reply_token, reply_text)


@handler.add(MessageEvent)
def handle_other(event: MessageEvent):
    # 画像・スタンプ等は無視
    return
