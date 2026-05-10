"""텔레그램 봇 — 스케줄 브리핑 + 종목 검색."""

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

import logging
import os
from datetime import date, datetime

import holidays
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)

import briefing as briefing_mod
import notifier
import db
import extractor
import summarizer
from downloader import download_pdf, build_pdf_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


KST = pytz.timezone("Asia/Seoul")
KR_HOLIDAYS = holidays.country_holidays("KR")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))


# ── 유틸 ──────────────────────────────────────────────

def is_business_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if d in KR_HOLIDAYS:
        return False
    return True


def _summarize_report(r: dict) -> dict | None:
    """PDF 다운로드 → 텍스트 추출 → Claude 요약."""
    if not r.get("pdf_url"):
        return None
    save_path = build_pdf_path(
        r.get("stock_code", "unknown"),
        r.get("published_at", "unknown"),
        r.get("securities_firm", "unknown"),
    )
    pdf_path = download_pdf(r["pdf_url"], save_path)
    if not pdf_path:
        return None
    text = extractor.extract_text(pdf_path)
    if not text:
        return None
    return summarizer.summarize(text, title=r.get("title", ""), stock_name=r.get("stock_name", ""))


# ── 스케줄 실행 ───────────────────────────────────────

async def scheduled_run(pages: int = 3):
    """크롤링 + 요약 + 브리핑 전송 (영업일만)."""
    from crawler import crawl_reports

    today = datetime.now(KST).date()
    if not is_business_day(today):
        logger.info("비영업일 스킵: %s", today)
        return

    logger.info("스케줄 실행 시작: %s", today)
    reports = crawl_reports(max_pages=pages)

    supabase = db.get_client()
    existing = db.get_existing_urls(supabase)
    new_reports = [r for r in reports if r["source_url"] not in existing]
    logger.info("신규 %d건 처리", len(new_reports))

    for r in new_reports:
        summary = _summarize_report(r)
        if summary:
            r["opinion"] = r.get("opinion") or summary.get("opinion")
            r["target_price"] = r.get("target_price") or summary.get("target_price")
        try:
            db.upsert_report(supabase, {**r, "summary": summary})
        except Exception as e:
            logger.error("DB 저장 실패: %s", e)

    msg = briefing_mod.build_message(today)
    if msg:
        notifier.send(msg)
    else:
        notifier.send(f"📭 {today} 브리핑\n발행된 리포트가 없습니다.")


# ── 텔레그램 핸들러 ───────────────────────────────────

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/search 종목명"""
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text("사용법: /search 종목명\n예) /search 삼성전자")
        return
    await _do_search(update, query)


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/briefing [YYYY-MM-DD]"""
    arg = context.args[0] if context.args else None
    try:
        target = date.fromisoformat(arg) if arg else datetime.now(KST).date()
    except ValueError:
        await update.message.reply_text("날짜 형식이 잘못됐어요. 예) /briefing 2026-05-08")
        return

    msg = briefing_mod.build_message(target)
    if msg:
        notifier.send(msg)
    else:
        await update.message.reply_text(f"📭 {target} 브리핑\n저장된 리포트가 없습니다.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 텍스트 → 종목 검색으로 처리."""
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    await _do_search(update, text)


async def _do_search(update: Update, query: str):
    import asyncio
    from crawler import search_reports

    await update.message.reply_text(
        f"🔍 <b>{query}</b> 리포트 검색 중...", parse_mode="HTML"
    )

    try:
        reports = search_reports(query, max_results=5)
    except Exception as e:
        await update.message.reply_text(f"검색 오류: {e}")
        return

    if not reports:
        await update.message.reply_text(
            f"최근 1개월 내 <b>{query}</b> 리포트가 없습니다.", parse_mode="HTML"
        )
        return

    # DB에 이미 요약된 것은 재사용, 없는 것만 새로 처리
    supabase = db.get_client()
    existing_rows = (
        supabase.table("reports")
        .select("source_url, summary")
        .in_("source_url", [r["source_url"] for r in reports])
        .execute()
        .data
    )
    cached = {row["source_url"]: row.get("summary") for row in existing_rows}

    await update.message.reply_text(
        f"📥 {len(reports)}건 중 신규 {sum(1 for r in reports if r['source_url'] not in cached)}건 요약 중...",
    )

    # 캐시 없는 건만 병렬 요약
    loop = asyncio.get_running_loop()
    async def get_summary(r):
        if r["source_url"] in cached:
            return cached[r["source_url"]]
        return await loop.run_in_executor(None, _summarize_report, r)

    summaries = await asyncio.gather(*[get_summary(r) for r in reports])

    lines = [f"📋 <b>{query}</b> 최근 리포트 {len(reports)}건\n"]
    for r, summary in zip(reports, summaries):
        tp = ""
        if summary and summary.get("target_price"):
            tp = f"  ▸ 목표주가 {int(summary['target_price']):,}원"
        opinion = f" [{summary['opinion']}]" if summary and summary.get("opinion") else ""

        lines.append(f"• <b>{r['title'][:45]}</b>{opinion}")
        lines.append(f"  {r['securities_firm']} · {r['published_at']}{tp}")
        if summary and summary.get("one_line"):
            lines.append(f"  {summary['one_line']}")
        if summary:
            for kp in (summary.get("key_points") or [])[:2]:
                lines.append(f"  · {kp}")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── 메인 ─────────────────────────────────────────────

def _start_health_server():
    """Render Web Service 무료 티어 요건 충족용 더미 HTTP 서버."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *a, **kw): pass

    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logger.info("Health server listening on :%d", port)


async def _post_init(application):
    """이벤트 루프가 시작된 후 스케줄러 기동."""
    scheduler = AsyncIOScheduler(timezone=KST)
    scheduler.add_job(scheduled_run, CronTrigger(hour=7,  minute=0, timezone=KST))
    scheduler.add_job(scheduled_run, CronTrigger(hour=18, minute=0, timezone=KST))
    scheduler.start()
    application.bot_data["scheduler"] = scheduler
    logger.info("스케줄러 시작 (07:00 / 18:00 KST)")


def main():
    _start_health_server()
    token = os.environ["TELEGRAM_BOT_TOKEN"]

    app = ApplicationBuilder().token(token).post_init(_post_init).build()
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("봇 시작")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
