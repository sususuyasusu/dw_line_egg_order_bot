"""
製造表 Sheets の AU/AV 列（確定便 卵黄/卵白 回転）に書き込む。

- タブ名は週月曜の MMDD (例: 6/1 月曜 → "0601")
- AO 列の日付セル ("6月2日" 等) を検索して行を特定
- AU = 卵黄回転、AV = 卵白回転 を上書き
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass

import gspread
from google.oauth2.service_account import Credentials

from parser import OrderItem


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]


@dataclass
class WriteResult:
    item: OrderItem
    tab: str
    row: int  # 1-based
    ok: bool
    error: str = ""


class SheetsClient:
    def __init__(self, spreadsheet_id: str, credentials_json: str):
        info = json.loads(credentials_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)

    @staticmethod
    def tab_name_for(date: dt.date) -> str:
        monday = date - dt.timedelta(days=date.weekday())
        return f"{monday.month:02d}{monday.day:02d}"

    @staticmethod
    def date_cell_text(date: dt.date) -> str:
        return f"{date.month}月{date.day}日"

    @staticmethod
    def _parse_cell_to_date(s: str) -> dt.date | None:
        """シートの表示文字列を date に変換。複数フォーマット対応。"""
        s = s.strip()
        if not s:
            return None
        formats = (
            "%Y/%m/%d", "%Y-%m-%d", "%Y/%-m/%-d", "%Y-%-m-%-d",
            "%m/%d/%Y", "%-m/%-d/%Y",
            "%m月%d日", "%-m月%-d日",
            "%m/%d", "%-m/%-d",
        )
        for fmt in formats:
            try:
                d = dt.datetime.strptime(s, fmt).date()
                return d
            except ValueError:
                continue
        return None

    def _find_row(self, ws: gspread.Worksheet, date: dt.date) -> int | None:
        """AO 列の各セルを date として解釈し、target date と日付一致する行を返す。
        年が省略表記なら月日のみ一致でOK。"""
        # AO列を unformatted で取得（シリアル数値 or 文字列）
        col = ws.get_values(
            "AO1:AO200", value_render_option="UNFORMATTED_VALUE"
        )
        sheet_epoch = dt.date(1899, 12, 30)  # Google Sheets エポック
        for i, row in enumerate(col, start=1):
            if not row:
                continue
            v = row[0]
            cand: dt.date | None = None
            if isinstance(v, (int, float)):
                try:
                    cand = sheet_epoch + dt.timedelta(days=int(v))
                except (OverflowError, ValueError):
                    cand = None
            elif isinstance(v, str):
                cand = self._parse_cell_to_date(v)
            if cand is None:
                continue
            # 年が来てない（月日のみ）の場合は month/day だけ一致でOK
            if cand.year < 1900:
                if cand.month == date.month and cand.day == date.day:
                    return i
            else:
                if cand == date:
                    return i
        return None

    def write_orders(self, items: list[OrderItem]) -> list[WriteResult]:
        results: list[WriteResult] = []
        # タブごとにグルーピングしてフェッチ回数を抑える
        by_tab: dict[str, list[OrderItem]] = {}
        for it in items:
            by_tab.setdefault(self.tab_name_for(it.date), []).append(it)

        for tab, group in by_tab.items():
            try:
                ws = self.sh.worksheet(tab)
            except gspread.WorksheetNotFound:
                for it in group:
                    results.append(WriteResult(it, tab, 0, False, f"タブ {tab} なし"))
                continue
            for it in group:
                row = self._find_row(ws, it.date)
                if row is None:
                    results.append(
                        WriteResult(it, tab, 0, False, f"AO列に {self.date_cell_text(it.date)} なし")
                    )
                    continue
                updates = []
                if it.yolk_packs:
                    updates.append(
                        {"range": f"AU{row}", "values": [[round(it.yolk_rot, 2)]]}
                    )
                if it.white_packs:
                    updates.append(
                        {"range": f"AV{row}", "values": [[round(it.white_rot, 2)]]}
                    )
                if updates:
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
                results.append(WriteResult(it, tab, row, True))
        return results


def get_client_from_env() -> SheetsClient:
    spreadsheet_id = os.environ["SPREADSHEET_ID"]
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    return SheetsClient(spreadsheet_id, creds_json)
