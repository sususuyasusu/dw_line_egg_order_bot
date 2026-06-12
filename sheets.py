"""
製造表 Sheets の配送便別合算セクション(実績) W/Y列に書き込む。

- タブ名は週月曜の MMDD (例: 6/1 月曜 → "0601")
- 火曜便=行69, 木曜便=行70, 土曜便=行71（固定）
- W = 卵黄g, Y = 卵白g を g単位で書込
- AU/AV (確定便 回転) はシート側の数式が W/Y を参照して自動計算する

シートの日付表示フォーマットや AU/AV の数式変更に影響されないようになった。
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

    # 火曜便=行69, 木曜便=行70, 土曜便=行71 （weekday: 月=0..日=6）
    BIN_ROW_BY_WEEKDAY = {1: 69, 3: 70, 5: 71}

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
                wd = it.date.weekday()
                row = self.BIN_ROW_BY_WEEKDAY.get(wd)
                if row is None:
                    results.append(
                        WriteResult(it, tab, 0, False, f"{it.weekday}曜日は火/木/土便ではない")
                    )
                    continue
                updates = []
                if it.yolk_packs:
                    updates.append({"range": f"W{row}", "values": [[it.yolk_g]]})
                if it.white_packs:
                    updates.append({"range": f"Y{row}", "values": [[it.white_g]]})
                if updates:
                    ws.batch_update(updates, value_input_option="USER_ENTERED")
                results.append(WriteResult(it, tab, row, True))
        return results


def get_client_from_env() -> SheetsClient:
    spreadsheet_id = os.environ["SPREADSHEET_ID"]
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    return SheetsClient(spreadsheet_id, creds_json)
