"""
LINE メッセージから卵発注を抽出。

期待フォーマット:

    卵発注

    ・2日（火）
    　卵黄 5kg×2
    　卵白 5kg×6

    ・4日（木）
    　卵黄 5kg×3
    　卵白 5kg×6

月省略は当日以降の最も近い未来日として解釈する。
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass


YOLK_G_PER_ROT = 400
WHITE_G_PER_ROT = 750
PACK_G = 5000  # 5kg

WEEKDAY_JP = ["月", "火", "水", "木", "金", "土", "日"]


@dataclass
class OrderItem:
    date: dt.date
    weekday: str           # 月..日
    yolk_packs: int        # 5kg pack count for 卵黄, 0 if absent
    white_packs: int       # 5kg pack count for 卵白, 0 if absent

    @property
    def yolk_g(self) -> int:
        return self.yolk_packs * PACK_G

    @property
    def white_g(self) -> int:
        return self.white_packs * PACK_G

    @property
    def yolk_rot(self) -> float:
        return self.yolk_g / YOLK_G_PER_ROT

    @property
    def white_rot(self) -> float:
        return self.white_g / WHITE_G_PER_ROT


HEADER_RE = re.compile(r"^\s*卵発注\s*$")

DATE_RE = re.compile(
    r"^\s*[・·•]\s*"
    r"(?:"
    r"(?:(?P<m1>\d{1,2})月)?\s*(?P<d1>\d{1,2})日"  # 「6月2日」「2日」
    r"|"
    r"(?P<m2>\d{1,2})[/／-](?P<d2>\d{1,2})"        # 「6/2」「6／2」「6-2」
    r")"
    r"(?:\s*[（(]\s*(?P<wd>[月火水木金土日])(?:曜日?)?\s*[）)])?\s*$"
)

ITEM_RE = re.compile(
    r"^\s*(?P<kind>卵黄|卵白)\s*"
    r"5\s*kg\s*[×xX*✕＊]\s*"
    r"(?P<n>\d+)\s*$"
)


def _normalize_line(line: str) -> str:
    # 全角空白→半角、ゼロ幅文字除去
    s = unicodedata.normalize("NFKC", line.replace("　", " "))
    return s.strip()


def starts_with_egg_order(text: str) -> bool:
    for raw in text.splitlines():
        s = _normalize_line(raw)
        if not s:
            continue
        return bool(HEADER_RE.match(s))
    return False


def _resolve_date(
    last: dt.date,
    today: dt.date,
    month: int | None,
    day: int,
) -> dt.date:
    """月省略時は last より後の最も近い (month, day) を採る。
    last が無い場合 (最初の日付) は today 以降の最も近い (month, day)。"""
    base = last if last else today - dt.timedelta(days=1)
    if month is not None:
        # 月明示: base 以降で最初の (month, day) を探す（多年跨ぎ防止のため2年以内）
        for year in (base.year, base.year + 1):
            try:
                cand = dt.date(year, month, day)
            except ValueError:
                continue
            if cand > base:
                return cand
        raise ValueError(f"unable to resolve {month}/{day} after {base}")
    # 月省略: base.month から最大13ヶ月先まで探す
    for offset in range(0, 14):
        y = base.year + (base.month - 1 + offset) // 12
        m = (base.month - 1 + offset) % 12 + 1
        try:
            cand = dt.date(y, m, day)
        except ValueError:
            continue
        if cand > base:
            return cand
    raise ValueError(f"unable to resolve day {day} after {base}")


def parse(text: str, today: dt.date | None = None) -> list[OrderItem]:
    """卵発注メッセージから OrderItem の列を返す。
    先頭が「卵発注」でない場合は空リスト。"""
    today = today or dt.date.today()

    lines = [_normalize_line(ln) for ln in text.splitlines()]
    # 先頭の空行スキップして「卵発注」確認
    idx = 0
    while idx < len(lines) and not lines[idx]:
        idx += 1
    if idx >= len(lines) or not HEADER_RE.match(lines[idx]):
        return []
    idx += 1

    items: list[OrderItem] = []
    cur_date: dt.date | None = None
    cur_weekday: str | None = None
    cur_yolk: int = 0
    cur_white: int = 0
    last_resolved: dt.date | None = None

    def flush():
        nonlocal cur_date, cur_weekday, cur_yolk, cur_white
        if cur_date and (cur_yolk or cur_white):
            items.append(
                OrderItem(
                    date=cur_date,
                    weekday=cur_weekday or WEEKDAY_JP[cur_date.weekday()],
                    yolk_packs=cur_yolk,
                    white_packs=cur_white,
                )
            )
        cur_date = None
        cur_weekday = None
        cur_yolk = 0
        cur_white = 0

    for line in lines[idx:]:
        if not line:
            continue
        m = DATE_RE.match(line)
        if m:
            flush()
            month = int(m["m1"] or m["m2"]) if (m["m1"] or m["m2"]) else None
            day = int(m["d1"] or m["d2"])
            cur_date = _resolve_date(last_resolved, today, month, day)
            last_resolved = cur_date
            actual_wd = WEEKDAY_JP[cur_date.weekday()]
            spec_wd = m["wd"]
            if spec_wd and spec_wd != actual_wd:
                # 曜日が合わない場合は警告だが採用は actual_wd
                cur_weekday = actual_wd
            else:
                cur_weekday = actual_wd
            continue
        m = ITEM_RE.match(line)
        if m:
            n = int(m["n"])
            if m["kind"] == "卵黄":
                cur_yolk = n
            else:
                cur_white = n
            continue
        # 想定外の行は無視
    flush()
    return items


def format_reply(items: list[OrderItem], note: str = "") -> str:
    lines: list[str] = ["✓ 発注を反映しました"]
    if note:
        lines.append(note)
    lines.append("")
    for it in items:
        lines.append(f"{it.date.month}/{it.date.day}({it.weekday})")
        if it.yolk_packs:
            lines.append(
                f"　卵黄 5kg×{it.yolk_packs}/{_fmt_rot(it.yolk_rot)}回転(1回転/400g)"
            )
        if it.white_packs:
            lines.append(
                f"　卵白 5kg×{it.white_packs}/{_fmt_rot(it.white_rot)}回転(1回転/750g)"
            )
    return "\n".join(lines)


def _fmt_rot(v: float) -> str:
    # 整数なら整数表記、それ以外は小数1桁
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.1f}"
