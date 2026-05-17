from __future__ import annotations
from pathlib import Path
"""Supabase에서 당일 리포트(없으면 최근일) 조회해 텔레그램 브리핑 생성."""

import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

KST = timezone(timedelta(hours=9))


def _fetch_rows(client, d: date) -> list[dict]:
    return (
        client.table("reports")
        .select("*")
        .eq("published_at", d.isoformat())
        .order("securities_firm")
        .execute()
        .data
    )


def _fetch_latest_date(client, before: date) -> Optional[str]:
    """before 이전 날짜 중 리포트가 존재하는 가장 최근 published_at(YYYY-MM-DD) 반환."""
    rows = (
        client.table("reports")
        .select("published_at")
        .lt("published_at", before.isoformat())
        .order("published_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0]["published_at"] if rows else None


def _dedupe_by_stock(rows: list[dict]) -> list[dict]:
    """동일 종목명은 첫 번째 것만 유지."""
    seen = set()
    out = []
    for r in rows:
        name = r.get("stock_name") or ""
        if name in seen:
            continue
        seen.add(name)
        out.append(r)
    return out


def build_message(target_date: Optional[date] = None) -> Optional[str]:
    """
    target_date(기본값: KST 오늘) 리포트 조회.
    당일 리포트 없으면 → 가장 최근 발행일의 리포트로 대체 (중복 종목 제외).
    """
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    d = target_date or datetime.now(KST).date()
    rows = _fetch_rows(client, d)

    fallback_date: Optional[date] = None
    if not rows:
        latest_str = _fetch_latest_date(client, d)
        if not latest_str:
            return None
        fallback_date = date.fromisoformat(latest_str)
        rows = _fetch_rows(client, fallback_date)
        rows = _dedupe_by_stock(rows)
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

    weekday_ko = ["월","화","수","목","금","토","일"]

    if fallback_date:
        head_d = fallback_date
        weekday = weekday_ko[head_d.weekday()]
        lines = [
            f"📭 <b>{d.year}년 {d.month}월 {d.day}일</b> 발행된 리포트가 없습니다.",
            f"가장 최근 발행일({head_d.year}-{head_d.month:02d}-{head_d.day:02d}, {weekday}) 리포트를 보여드릴게요.\n",
            f"📊 <b>리포트 {len(rows)}건</b> <i>(중복 종목 제외)</i>\n",
        ]
    else:
        head_d = d
        weekday = weekday_ko[head_d.weekday()]
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
    ]
    return "\n".join(lines)
