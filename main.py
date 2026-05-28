"""
LINE Bot: どら山社員グループの「卵発注」メッセージを
製造表 Sheets の確定便 (AU/AV) 列に反映する。
"""
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration
from linebot.v3.webhooks import (
    GroupSource,
    MessageEvent,
    TextMessageContent,
)

from parser import parse, starts_with_egg_order
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
    except Exception:
        log.exception("parse failed")
        return

    if not items:
        log.info("no items parsed from text")
        return

    try:
        client = get_client_from_env()
        results = client.write_orders(items)
    except Exception:
        log.exception("sheets write failed")
        return

    for r in results:
        if r.ok:
            log.info(
                "wrote tab=%s row=%s date=%s yolk=%s white=%s",
                r.tab, r.row, r.item.date, r.item.yolk_rot, r.item.white_rot,
            )
        else:
            log.warning("skip date=%s: %s", r.item.date, r.error)


