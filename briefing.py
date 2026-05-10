from __future__ import annotations
from pathlib import Path
"""Supabase에서 당일 리포트를 조회해 텔레그램 브리핑 메시지 생성."""

import os
from datetime import date
from typing import Optional
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


def build_message(target_date: Optional[date] = None) -> Optional[str]:
    """
    target_date(기본값: 오늘)의 리포트를 조회해 브리핑 텍스트 반환.
    리포트가 없으면 None 반환.
    """
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    d = target_date or date.today()
    rows = (
        client.table("reports")
        .select("*")
        .eq("published_at", d.isoformat())
        .order("securities_firm")
        .execute()
        .data
    )

    if not rows:
        return None

    buy, hold, sell, other = [], [], [], []
    for r in rows:
        s = r.get("summary") or {}
        opinion = (s.get("opinion") or "").upper().strip()
        entry = {
            "stock":  r.get("stock_name", ""),
            "firm":   r.get("securities_firm", ""),
            "title":  r.get("title", ""),
            "one_line": s.get("one_line", ""),
            "opinion":  opinion,
            "target":   s.get("target_price") or r.get("target_price"),
            "key_points": (s.get("key_points") or [])[:2],
            "risks":      (s.get("risks") or [])[:1],
        }
        if "BUY" in opinion:   buy.append(entry)
        elif "HOLD" in opinion: hold.append(entry)
        elif "SELL" in opinion: sell.append(entry)
        else:                   other.append(entry)

    weekday = ["월","화","수","목","금","토","일"][d.weekday()]
    lines = [
        f"📊 <b>브리핑 — {d.year}년 {d.month}월 {d.day}일 ({weekday})</b>",
        f"신규 리포트 <b>{len(rows)}건</b>\n",
    ]

    def section(emoji, label, items, limit=10):
        if not items:
            return
        lines.append(f"{emoji} <b>{label} ({len(items)}건)</b>")
        for r in items[:limit]:
            tp = f"  ▸ 목표주가 {int(r['target']):,}원" if r["target"] else ""
            lines.append(f"• <b>{r['stock']}</b> ({r['firm']}){tp}")
            if r["one_line"]:
                lines.append(f"  {r['one_line']}")
            for kp in r["key_points"]:
                lines.append(f"  · {kp}")
        if len(items) > limit:
            lines.append(f"  <i>외 {len(items)-limit}건 더...</i>")
        lines.append("")

    section("🟢", "매수 BUY",  buy)
    section("🟡", "중립 HOLD", hold)
    section("🔴", "매도 SELL", sell)
    section("⚪", "기타",      other)

    lines += [
        "─────────────────",
        f"BUY <b>{len(buy)}</b> · HOLD <b>{len(hold)}</b> · SELL <b>{len(sell)}</b>",
        "<i>브리핑 · Powered by Ollama</i>",
    ]
    return "\n".join(lines)
