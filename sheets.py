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

    def _find_row(self, ws: gspread.Worksheet, date: dt.date) -> int | None:
        """AO 列で date_cell_text の行を返す。見つからなければ None。"""
        target = self.date_cell_text(date)
        # AO列のみフェッチ
        col = ws.col_values(41)  # AO = 41
        for i, v in enumerate(col, start=1):
            if v.strip() == target:
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
