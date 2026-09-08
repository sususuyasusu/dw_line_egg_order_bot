"""
freee ファイルボックス取込（セゾンカード領収書 @916egopi）: LINE画像 → freee /api/1/receipts。

2026-09-08 に dw_line_freee_uploader（Render無料枠でスリープ→取りこぼし）から、
常時稼働のこのサービス（Starter・支払済み・GitHub Actions keep-warm）へ同居移設。追加費用ゼロ。
卵発注・既存の receipt_intake（Dropbox取込）とは LINEチャネルも環境変数も別（FREEE_RCPT_ / FREEE_ プレフィックス）。
環境変数が無ければ configured()=False となり main.py はマウントしない＝卵発注は一切影響を受けない。

freeeのrefresh_tokenは使うたびローテーションするため、正本はDropboxの状態ファイル
(/D& W/社内/_bot-state/dw_line_freee_uploader/freee_token.json) に置き、旧アップローダーと同じ物を共有する。
※旧アップローダーは移設後に停止し、このプロセスだけがトークンを回す（二重ローテーション事故の防止）。
"""
import datetime
import hashlib
import json
import logging
import os
import threading

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("freee-receipt-intake")

CHANNEL_SECRET = os.environ.get("FREEE_RCPT_LINE_CHANNEL_SECRET", "")
CHANNEL_ACCESS_TOKEN = os.environ.get("FREEE_RCPT_LINE_CHANNEL_ACCESS_TOKEN", "")
FREEE_CLIENT_ID = os.environ.get("FREEE_CLIENT_ID", "")
FREEE_CLIENT_SECRET = os.environ.get("FREEE_CLIENT_SECRET", "")
FREEE_REFRESH_TOKEN = os.environ.get("FREEE_REFRESH_TOKEN", "")  # ブートストラップのみ
FREEE_COMPANY_ID = os.environ.get("FREEE_COMPANY_ID", "800646")

DROPBOX_REFRESH_TOKEN = os.environ.get("DROPBOX_REFRESH_TOKEN", "")
DROPBOX_APP_KEY = os.environ.get("DROPBOX_APP_KEY", "")
DROPBOX_APP_SECRET = os.environ.get("DROPBOX_APP_SECRET", "")
DROPBOX_ROOT_NAMESPACE_ID = os.environ.get("DROPBOX_ROOT_NAMESPACE_ID")
TOKEN_STATE_PATH = os.environ.get(
    "FREEE_TOKEN_STATE_PATH",
    "/D& W/社内/_bot-state/dw_line_freee_uploader/freee_token.json",
)
SEEN_FOLDER = os.environ.get(
    "FREEE_RCPT_SEEN_FOLDER",
    "/D& W/社内/_bot-state/dw_line_freee_uploader/_seen",
)

FREEE_API_BASE = "https://api.freee.co.jp"
FREEE_OAUTH_BASE = "https://accounts.secure.freee.co.jp"


def configured() -> bool:
    return bool(
        CHANNEL_SECRET and CHANNEL_ACCESS_TOKEN
        and FREEE_CLIENT_ID and FREEE_CLIENT_SECRET and FREEE_REFRESH_TOKEN
        and DROPBOX_REFRESH_TOKEN
    )


router = APIRouter(prefix="/freee-receipt")

_DIAG = {
    "webhook_calls": 0,
    "image_handler_calls": 0,
    "uploaded": 0,
    "duplicates": 0,
    "failed": 0,
    "last_error": None,
    "last_upload": None,
    "rotations": 0,
}

if configured():
    from linebot.v3 import WebhookHandler
    from linebot.v3.messaging import Configuration

    handler = WebhookHandler(CHANNEL_SECRET)
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
else:
    handler = None
    configuration = None


def _dbx():
    import dropbox

    dbx = dropbox.Dropbox(
        app_key=DROPBOX_APP_KEY,
        app_secret=DROPBOX_APP_SECRET,
        oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
    )
    if DROPBOX_ROOT_NAMESPACE_ID:
        from dropbox.common import PathRoot
        dbx = dbx.with_path_root(PathRoot.root(DROPBOX_ROOT_NAMESPACE_ID))
    return dbx


# ── freee トークン（Dropbox状態ファイルでローテーション） ──────────────
_token_lock = threading.Lock()
_access_token = {"value": None}


def _load_refresh_token() -> str:
    try:
        _, resp = _dbx().files_download(TOKEN_STATE_PATH)
        data = json.loads(resp.content.decode("utf-8"))
        rt = data.get("refresh_token")
        if rt and len(rt) > 10:
            return rt
    except Exception as e:  # noqa: BLE001
        log.warning("freee token load failed: %s", str(e)[:200])
    return FREEE_REFRESH_TOKEN


def _persist_refresh_token(rt: str) -> None:
    import dropbox

    body = json.dumps(
        {"refresh_token": rt, "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
         "service": "dw_line_egg_order_bot/freee_receipt_intake"},
        ensure_ascii=False, indent=2,
    ).encode("utf-8")
    _dbx().files_upload(body, TOKEN_STATE_PATH, mode=dropbox.files.WriteMode.overwrite)


def _refresh_access_token() -> str:
    import requests

    with _token_lock:
        rt = _load_refresh_token()
        resp = requests.post(
            f"{FREEE_OAUTH_BASE}/public_api/token",
            data={
                "grant_type": "refresh_token",
                "client_id": FREEE_CLIENT_ID,
                "client_secret": FREEE_CLIENT_SECRET,
                "refresh_token": rt,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        _access_token["value"] = data["access_token"]
        new_rt = data.get("refresh_token")
        if new_rt and new_rt != rt:
            _persist_refresh_token(new_rt)
            _DIAG["rotations"] += 1
        return _access_token["value"]


def _ensure_access_token() -> str:
    if not _access_token["value"]:
        return _refresh_access_token()
    return _access_token["value"]


def _freee_get(path: str, params: dict):
    import requests

    for attempt in range(2):
        tok = _ensure_access_token()
        r = requests.get(
            f"{FREEE_API_BASE}{path}",
            headers={"Authorization": f"Bearer {tok}"},
            params={"company_id": FREEE_COMPANY_ID, **params},
            timeout=30,
        )
        if r.status_code == 401 and attempt == 0:
            _access_token["value"] = None
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("freee get failed")


def _freee_upload_receipt(buffer: bytes, filename: str, content_type: str, description: str):
    import requests

    for attempt in range(2):
        tok = _ensure_access_token()
        r = requests.post(
            f"{FREEE_API_BASE}/api/1/receipts",
            headers={"Authorization": f"Bearer {tok}", "accept": "application/json"},
            data={"company_id": str(FREEE_COMPANY_ID), "description": description},
            files={"receipt": (filename, buffer, content_type)},
            timeout=60,
        )
        if r.status_code == 401 and attempt == 0:
            _access_token["value"] = None
            continue
        r.raise_for_status()
        body = r.json() or {}
        rc = body.get("receipt") or body
        return rc.get("id") if isinstance(rc, dict) else None
    raise RuntimeError("freee upload failed")


# ── 重複防止（LINE再送 / 同一画像） Dropbox _seen マーカー ──────────────
def _already_seen(key: str) -> bool:
    try:
        _dbx().files_get_metadata(f"{SEEN_FOLDER}/{key}")
        return True
    except Exception:  # noqa: BLE001
        return False


def _mark_seen(key: str) -> None:
    import dropbox

    try:
        _dbx().files_upload(b"", f"{SEEN_FOLDER}/{key}", mode=dropbox.files.WriteMode.overwrite)
    except Exception:  # noqa: BLE001
        pass


def _detect_type(buf: bytes):
    if buf[:3] == b"\xff\xd8\xff":
        return ("image/jpeg", "jpg")
    if buf[:8] == b"\x89PNG\r\n\x1a\n":
        return ("image/png", "png")
    if buf[:4] == b"%PDF":
        return ("application/pdf", "pdf")
    if buf[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1"):
        return ("image/heic", "heic")
    return (None, None)


def _fetch_content(message_id: str) -> bytes:
    from linebot.v3.messaging import ApiClient, MessagingApiBlob

    with ApiClient(configuration) as api_client:
        return MessagingApiBlob(api_client).get_message_content(message_id=message_id)


def _group_name(source) -> str:
    try:
        from linebot.v3.messaging import ApiClient, MessagingApi
        gid = getattr(source, "group_id", None)
        if not gid:
            return ""
        with ApiClient(configuration) as api_client:
            s = MessagingApi(api_client).get_group_summary(gid)
            return getattr(s, "group_name", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _reply(reply_token: str, text: str) -> None:
    try:
        from linebot.v3.messaging import (ApiClient, MessagingApi, ReplyMessageRequest, TextMessage)
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=[TextMessage(text=text)])
            )
    except Exception as e:  # noqa: BLE001
        log.warning("reply failed: %s", str(e)[:150])


@router.get("/healthz")
def healthz():
    return {"status": "ok", "service": "freee-receipt-intake (bundled)", "diag": _DIAG}


@router.get("/selftest")
def selftest():
    out = {}
    try:
        acct = _dbx().users_get_current_account()
        out["dropbox"] = {"ok": True, "email": acct.email}
    except Exception as e:  # noqa: BLE001
        out["dropbox"] = {"ok": False, "error": str(e)[:200]}
    try:
        recs = _freee_get("/api/1/receipts", {
            "start_date": (datetime.date.today() - datetime.timedelta(days=7)).isoformat(),
            "end_date": (datetime.date.today() + datetime.timedelta(days=1)).isoformat(),
            "limit": 3,
        })
        out["freee"] = {"ok": True, "recent": len(recs.get("receipts", [])), "rotations": _DIAG["rotations"]}
    except Exception as e:  # noqa: BLE001
        out["freee"] = {"ok": False, "error": str(e)[:200]}
    return out


@router.post("/webhook")
async def webhook(request: Request):
    from linebot.v3.exceptions import InvalidSignatureError

    signature = request.headers.get("X-Line-Signature", "")
    body = (await request.body()).decode("utf-8")
    _DIAG["webhook_calls"] += 1
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return {"status": "ok"}


if configured():
    from linebot.v3.webhooks import (FileMessageContent, ImageMessageContent, MessageEvent)

    def _process(event, content_getter):
        _DIAG["image_handler_calls"] += 1
        message_id = event.message.id
        if _already_seen(f"msg-{message_id}"):
            _DIAG["duplicates"] += 1
            return
        buf = content_getter(message_id)
        ctype, ext = _detect_type(buf)
        if not ctype:
            _reply(event.reply_token, "領収書の画像を送ってください。")
            return
        h = hashlib.sha256(buf).hexdigest()
        if _already_seen(f"img-{h[:16]}"):
            _DIAG["duplicates"] += 1
            _reply(event.reply_token, "この領収書はすでに取り込み済みです。")
            return
        jst = datetime.timezone(datetime.timedelta(hours=9))
        ts_ms = getattr(event, "timestamp", None)
        ts = (datetime.datetime.fromtimestamp(ts_ms / 1000, tz=jst) if ts_ms
              else datetime.datetime.now(jst))
        gname = _group_name(event.source)
        where = f"[{gname}]" if gname else "[個別]"
        uid = getattr(event.source, "user_id", None) or "unknown"
        desc = (f"LINE {uid[:8]} {where} {ts.strftime('%Y-%m-%d %H:%M')} "
                f"msg={message_id} img={h[:12]}")
        filename = f"{h[:16]}_{message_id}.{ext}"
        try:
            rid = _freee_upload_receipt(buf, filename, ctype, desc)
            _mark_seen(f"msg-{message_id}")
            _mark_seen(f"img-{h[:16]}")
            _DIAG["uploaded"] += 1
            _DIAG["last_upload"] = datetime.datetime.utcnow().isoformat() + "Z"
            log.info("freee upload ok receipt_id=%s msg=%s", rid, message_id)
            _reply(event.reply_token,
                   "領収書画像をfreeeファイルボックスへアップロードしました。freee側でOCR後、内容確認・計上してください。")
        except Exception as e:  # noqa: BLE001
            _DIAG["failed"] += 1
            _DIAG["last_error"] = str(e)[:300]
            log.exception("freee upload failed msg=%s", message_id)
            _reply(event.reply_token, "freeeへのアップロードに失敗しました。管理者に確認してください。")
            raise

    @handler.add(MessageEvent, message=ImageMessageContent)
    def handle_image(event: MessageEvent):
        _process(event, _fetch_content)

    @handler.add(MessageEvent, message=FileMessageContent)
    def handle_file(event: MessageEvent):
        _process(event, _fetch_content)
