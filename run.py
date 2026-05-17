from pathlib import Path
"""
브리핑 자동 실행 진입점.
- 영업일(평일 + 한국 공휴일 제외)에만 실행
- 크롤링 → 요약 → 텔레그램 전송
- launchd 또는 cron에서 호출

사용법:
    python run.py           # 오늘 날짜로 실행
    python run.py --date 2026-05-08   # 특정 날짜 테스트
    python run.py --skip-crawl        # 크롤링 생략, 브리핑만 전송
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

import holidays
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import briefing as briefing_mod
import notifier
import db
import extractor
import summarizer
from downloader import build_pdf_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

KR_HOLIDAYS = holidays.country_holidays("KR")


def is_business_day(d: date) -> bool:
    """주말·한국 공휴일이면 False."""
    if d.weekday() >= 5:  # 토(5), 일(6)
        return False
    if d in KR_HOLIDAYS:
        logger.info("오늘은 공휴일(%s)입니다. 스킵.", KR_HOLIDAYS.get(d))
        return False
    return True


def crawl_and_summarize(pages: int = 3) -> int:
    """크롤링 + 요약 + DB 저장. 새로 저장된 건수 반환."""
    import crawler

    logger.info("크롤링 시작...")
    reports = crawler.crawl_reports(max_pages=pages)

    supabase = db.get_client()
    existing = db.get_existing_urls(supabase)
    new_reports = [r for r in reports if r["source_url"] not in existing]
    logger.info("신규 %d건 처리 시작", len(new_reports))

    saved = 0
    for r in new_reports:
        if not r.get("pdf_url"):
            continue

        save_path = build_pdf_path(
            r.get("stock_code", "unknown"),
            r.get("published_at", "unknown"),
            r.get("securities_firm", "unknown"),
        )
        from downloader import download_pdf
        pdf_path = download_pdf(r["pdf_url"], save_path)
        if not pdf_path:
            continue

        text = extractor.extract_text(pdf_path)
        if not text:
            continue

        summary = summarizer.summarize(text, title=r.get("title",""), stock_name=r.get("stock_name",""))
        if summary:
            r["opinion"] = r.get("opinion") or summary.get("opinion")
            r["target_price"] = r.get("target_price") or summary.get("target_price")

        try:
            db.upsert_report(supabase, {**r, "summary": summary})
            saved += 1
        except Exception as e:
            logger.error("DB 저장 실패: %s", e)

    return saved


def send_briefing(target_date: date) -> None:
    msg = briefing_mod.build_message(target_date)
    if not msg:
        # 오늘도 없고 과거 리포트도 없는 극단적인 케이스 (DB 비어있음)
        logger.info("DB에 리포트 자체가 없음 (%s)", target_date)
        notifier.send(f"📭 {target_date} 브리핑\nDB에 리포트가 없습니다.")
        return

    notifier.send(msg)
    logger.info("텔레그램 브리핑 전송 완료")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (기본값: 오늘)")
    parser.add_argument("--skip-crawl", action="store_true", help="크롤링 생략")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--force", action="store_true", help="비영업일도 강제 실행")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else datetime.now(KST).date()

    if not args.force and not is_business_day(target):
        logger.info("%s은 비영업일입니다. 종료.", target)
        return

    if not args.skip_crawl:
        saved = crawl_and_summarize(pages=args.pages)
        logger.info("신규 저장: %d건", saved)

    send_briefing(target)


if __name__ == "__main__":
    main()
