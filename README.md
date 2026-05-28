# dw_line_egg_order_bot

どら山社員グループの「卵発注」LINE メッセージを Google Sheets（製造表）の確定便 AU/AV 列に反映する Bot。

## 動作概要

LINE グループに以下のフォーマットで投稿すると、対応する週タブ（例: `0601`）の AO 列の日付行を検索し、**AU 列（確定便 卵黄回転）と AV 列（確定便 卵白回転）** を上書きする。

```
卵発注

・2日（火）
　卵黄 5kg×2
　卵白 5kg×6

・4日（木）
　卵黄 5kg×3
　卵白 5kg×6

・6日（土）
　卵黄 5kg×4
　卵白 5kg×8
```

返信例：

```
✓ 発注を反映しました

6/2(火)
　卵黄 5kg×2/25回転(1回転/400g)
　卵白 5kg×6/40回転(1回転/750g)
6/4(木)
　卵黄 5kg×3/37.5回転(1回転/400g)
　卵白 5kg×6/40回転(1回転/750g)
6/6(土)
　卵黄 5kg×4/50回転(1回転/400g)
　卵白 5kg×8/53.3回転(1回転/750g)
```

換算式: `卵黄回転 = 5000g × N / 400g`、`卵白回転 = 5000g × N / 750g`

## ファイル構成

| ファイル | 役割 |
|---|---|
| `main.py` | FastAPI Webhook + LINE 受信ハンドラ |
| `parser.py` | メッセージパース・回転換算・返信整形 |
| `sheets.py` | Sheets 書込（タブ判定・行特定・AU/AV 上書き） |
| `Dockerfile` | python:3.11-slim |
| `render.yaml` | Render Web Service 設定 |
| `requirements.txt` | 依存 |

## セットアップ手順

### 1. LINE Messaging API チャネル新規作成

1. https://developers.line.biz/console/ にアクセス
2. プロバイダーを新規作成（または既存のものを使用）
3. 「Messaging API」チャネルを新規作成
4. チャネル名は任意（例: `どら山卵発注Bot`）
5. 作成後、以下の値をコピー：
   - **チャネルシークレット**（チャネル基本設定）
   - **チャネルアクセストークン（長期）**（Messaging API 設定 → 発行）
6. **応答メッセージを無効化**（Messaging API 設定）
7. **Webhook を有効化**（Messaging API 設定、URL は次の手順で設定）
8. **グループへの参加を許可**（チャネル基本設定 → グループ・複数人トーク参加 → 利用する）

### 2. Render デプロイ

1. このディレクトリを GitHub リポジトリにpush
2. https://render.com で「New Web Service」→ GitHub から本リポジトリを選択
3. Docker を自動検出。プラン: Free
4. 環境変数を設定：

| 変数 | 値 |
|---|---|
| `LINE_CHANNEL_SECRET` | LINE Developers で取得 |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developers で取得 |
| `GOOGLE_CREDENTIALS_JSON` | サービスアカウント JSON 全文（`dorayama-sheets-bot@dw-dorayama-automation.iam` の private key） |
| `SPREADSHEET_ID` | `1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM` |
| `ALLOWED_GROUP_IDS` | （任意）どら山社員グループの groupId。空ならグループ制限なし |

5. デプロイ後 URL を取得（例: `https://dw-line-egg-order-bot.onrender.com`）
6. LINE Developers Console の Webhook URL に `<Render URL>/webhook` を設定

### 3. グループ参加と動作確認

1. LINE Developers Console → Messaging API 設定 → QR コードで Bot を友だち追加
2. どら山社員グループに Bot を招待
3. （`ALLOWED_GROUP_IDS` を絞る場合）グループに参加直後、`MemberJoined` event の `source.groupId` を Render ログで確認し、`ALLOWED_GROUP_IDS` に設定
4. グループで「卵発注」テスト投稿 → スプレッドシートに反映 + 返信を確認

## スプレッドシート前提

- スプレッドシート ID: `1PRDhGP_4xiO_ZjJP3NB9Id3PmaPa5W7hNyrqFQ5EyqM`
- 週タブ名: `MMDD`（月曜起算の月日、例 `0601`）
- AO 列: 日付テキスト（`6月2日` 等）
- AU 列: 確定便 卵黄（回転）← bot 書込
- AV 列: 確定便 卵白（回転）← bot 書込
- SA `dorayama-sheets-bot@dw-dorayama-automation.iam.gserviceaccount.com` に編集権限が付与済み

## ローカル検証

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
SPREADSHEET_ID=... \
GOOGLE_CREDENTIALS_JSON="$(cat path/to/sa.json)" \
.venv/bin/python3 -c "
import datetime as dt
from parser import parse, format_reply
from sheets import get_client_from_env
items = parse('卵発注\n・2日（火）\n　卵黄 5kg×2\n　卵白 5kg×6', today=dt.date(2026,5,28))
results = get_client_from_env().write_orders(items)
for r in results: print(r)
"
```

## 制限

- Render free tier は 15 分無アクセスでスリープ。初回応答が 30 秒〜1 分遅延する。
- 月省略の日付は「最も近い未来日」として解釈する（例: 28日 → 当日 or 来月28日）。
- 曜日カッコ書きは表示確認のみ（実際の曜日は date から再計算）。
- 月・水・金・日のような非確定便日に書いた場合も AU/AV 該当行に書込まれる（通常は火/木/土のみ運用想定）。

## トラブルシュート

| 症状 | 対処 |
|---|---|
| LINE で「卵発注」を送っても無反応 | Render ログで Invalid signature か確認。シークレット不一致 |
| 「⚠️ シート書込失敗」と返信 | SA の権限・SPREADSHEET_ID・JSON の整形を確認 |
| 「タブ XXXX なし」と返信 | 対象週タブが未作成。Sheets 側で先にタブを作る |
| 「AO列に X月Y日 なし」 | 該当日付セルが想定の表記でない。AO 列を目視確認 |
