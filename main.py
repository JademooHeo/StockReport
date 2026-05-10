from pathlib import Path
"""
네이버 증권 리서치 리포트 수집·요약 파이프라인 진입점.

실행:
    python main.py [--pages N]

사전 준비:
    1. pip install -r requirements.txt
    2. playwright install chromium
    3. .env 파일에 ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY 입력
    4. Supabase SQL 에디터에서 db.py 상단 주석의 CREATE TABLE 실행
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import crawler
import db
import downloader
import extractor
import summarizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def run(max_pages: int) -> None:
    stats = {"total": 0, "new": 0, "skipped": 0, "pdf_fail": 0, "summary_fail": 0, "db_fail": 0}

    # ── 1. 크롤링 ──────────────────────────────────────────────────
    logger.info("=== 크롤링 시작 (최대 %d페이지) ===", max_pages)
    reports = await crawler.crawl_reports(max_pages=max_pages)
    stats["total"] = len(reports)

    if not reports:
        logger.warning("수집된 리포트가 없습니다. 종료합니다.")
        return

    # ── 2. DB 중복 확인 ────────────────────────────────────────────
    logger.info("=== DB 중복 확인 ===")
    supabase = db.get_client()
    existing_urls = db.get_existing_urls(supabase)
    logger.info("기존 DB 항목: %d건", len(existing_urls))

    new_reports = [r for r in reports if r["source_url"] not in existing_urls]
    stats["skipped"] = len(reports) - len(new_reports)
    logger.info("신규 처리 대상: %d건 / 스킵: %d건", len(new_reports), stats["skipped"])

    # ── 3. 각 리포트 처리 ──────────────────────────────────────────
    for idx, report in enumerate(new_reports, 1):
        label = f"[{idx}/{len(new_reports)}] {report['stock_name']} - {report['title'][:30]}"
        logger.info("--- %s ---", label)

        # PDF 다운로드
        if not report.get("pdf_url"):
            logger.warning("  PDF URL 없음, 스킵")
            stats["pdf_fail"] += 1
            continue

        save_path = downloader.build_pdf_path(
            report.get("stock_code", "unknown"),
            report.get("published_at", "unknown"),
            report.get("securities_firm", "unknown"),
        )
        pdf_path = downloader.download_pdf(report["pdf_url"], save_path)

        if not pdf_path:
            stats["pdf_fail"] += 1
            continue

        # PDF 텍스트 추출
        text = extractor.extract_text(pdf_path)
        if not text:
            logger.warning("  텍스트 추출 실패, 스킵")
            stats["pdf_fail"] += 1
            continue

        # Claude API 요약
        summary = summarizer.summarize(
            text,
            title=report.get("title", ""),
            stock_name=report.get("stock_name", ""),
        )
        if not summary:
            stats["summary_fail"] += 1
            # 요약 실패해도 메타데이터는 저장
            logger.warning("  요약 실패, summary=null로 저장")

        # 요약 결과로 opinion/target_price 보완 (크롤링값 우선, 없으면 요약값 사용)
        if summary:
            if not report.get("opinion"):
                report["opinion"] = summary.get("opinion")
            if report.get("target_price") is None:
                report["target_price"] = summary.get("target_price")

        record = {**report, "summary": summary}

        # Supabase 저장
        try:
            is_new = db.upsert_report(supabase, record)
            stats["new"] += 1
            logger.info("  DB 저장: %s", "신규" if is_new else "갱신")
        except Exception as e:
            logger.error("  DB 저장 실패: %s", e)
            stats["db_fail"] += 1

    # ── 4. 결과 요약 ───────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 50)
    logger.info("실행 완료")
    logger.info("  전체 수집:    %d건", stats["total"])
    logger.info("  스킵(중복):   %d건", stats["skipped"])
    logger.info("  DB 저장:      %d건", stats["new"])
    logger.info("  PDF 실패:     %d건", stats["pdf_fail"])
    logger.info("  요약 실패:    %d건", stats["summary_fail"])
    logger.info("  DB 오류:      %d건", stats["db_fail"])
    logger.info("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(description="네이버 증권 리서치 리포트 수집 파이프라인")
    parser.add_argument("--pages", type=int, default=3, help="크롤링할 페이지 수 (기본값: 3)")
    args = parser.parse_args()

    asyncio.run(run(args.pages))


if __name__ == "__main__":
    main()
