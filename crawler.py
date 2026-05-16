"""네이버 증권 종목분석 리포트 목록 크롤러 (Firecrawl)."""

from pathlib import Path
import logging
import re
import time
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

import os
from firecrawl import FirecrawlApp

logger = logging.getLogger(__name__)

BASE_URL = "https://finance.naver.com/research/company_list.naver"
NAVER_ROOT = "https://finance.naver.com"
DELAY = 1.0


def _parse_stock_code(href: str) -> Optional[str]:
    m = re.search(r"code=(\d{6})", href or "")
    return m.group(1) if m else None


def _normalize_date(text: str) -> Optional[str]:
    """YY.MM.DD → YYYY-MM-DD"""
    m = re.match(r"(\d{2,4})[./\-](\d{2})[./\-](\d{2})", text.strip())
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y
    return f"{y}-{mo}-{d}"


def _parse_markdown_table(markdown: str) -> list[dict]:
    """
    Firecrawl이 반환한 마크다운 테이블에서 리포트 목록 파싱.
    예시 행:
      | [삼성전자](https://...code=005930) | [리포트제목](https://.../company_read.naver?...) | 미래에셋 | [![pdf](...)](https://...pdf) | 26.05.08 | 1234 |
    """
    reports = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        # 구분선 스킵
        if re.match(r"^\|[\s\-|]+\|$", line):
            continue

        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c != ""]
        if len(cols) < 5:
            continue

        # col[0]: 종목명 [종목명](url)
        stock_m = re.search(r"\[([^\]]+)\]\(([^)]+)\)", cols[0])
        if not stock_m:
            continue
        stock_name = stock_m.group(1).strip()
        stock_url = stock_m.group(2)
        stock_code = _parse_stock_code(stock_url)

        # col[1]: 리포트 제목 [제목](url)
        title_m = re.search(r"\[([^\]]+)\]\(([^)]+)\)", cols[1])
        if not title_m:
            continue
        title = title_m.group(1).strip()
        source_url = title_m.group(2)
        # 상대경로 처리
        if source_url.startswith("/"):
            source_url = NAVER_ROOT + source_url

        # col[2]: 증권사
        securities_firm = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cols[2]).strip()

        # col[3]: PDF 링크 [![pdf](img)](pdf_url) 또는 [텍스트](pdf_url)
        pdf_url = None
        pdf_m = re.search(r"\]\(([^)]+\.pdf[^)]*)\)", cols[3], re.I)
        if pdf_m:
            pdf_url = pdf_m.group(1)
        else:
            # 이미지 래퍼 안의 마지막 괄호 URL 추출
            all_urls = re.findall(r"\(([^)]+)\)", cols[3])
            for u in reversed(all_urls):
                if u.startswith("http") and ".pdf" not in u.lower():
                    # PDF가 아닌 http URL이면 파일 다운로드 링크일 수 있음
                    pass
                if u.startswith("http"):
                    pdf_url = u
                    break

        # col[4]: 발행일
        published_at = _normalize_date(cols[4])

        reports.append({
            "title": title,
            "stock_name": stock_name,
            "stock_code": stock_code,
            "securities_firm": securities_firm,
            "analyst": None,
            "opinion": None,
            "target_price": None,
            "published_at": published_at,
            "pdf_url": pdf_url,
            "source_url": source_url,
        })

    return reports


def search_reports(keyword: str, max_results: int = 5) -> list[dict]:
    """
    특정 종목명으로 네이버 증권 리포트 검색.
    최근 1개월 이내, 최대 max_results건 반환.
    """
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=30)).isoformat()

    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY가 설정되지 않았습니다.")

    app = FirecrawlApp(api_key=api_key)
    from urllib.parse import quote
    # Naver는 EUC-KR 인코딩 사용
    encoded = quote(keyword.encode("euc-kr"))
    url = f"{BASE_URL}?keyword={encoded}&searchType=keyword"
    logger.info("종목 검색 URL: %s", url)

    try:
        result = app.scrape(url, formats=["markdown"], only_main_content=False)
        markdown = result.markdown if hasattr(result, "markdown") else (result.get("markdown") or "")
    except Exception as e:
        logger.error("Firecrawl 검색 오류: %s", e)
        return []

    logger.info("검색 결과 길이: %d자", len(markdown))
    all_reports = _parse_markdown_table(markdown)
    logger.info("파싱된 리포트: %d건", len(all_reports))

    # 종목명이 검색어와 정확히 일치하는 것만 (예: '삼성전자'를 검색하면 '삼성생명' 제외)
    name_matched = [r for r in all_reports if r.get("stock_name") == keyword]
    logger.info("종목명 일치 후: %d건", len(name_matched))

    recent = [r for r in name_matched if r.get("published_at") and r["published_at"] >= cutoff]
    logger.info("최근 1개월 필터 후: %d건", len(recent))
    return recent[:max_results]


def crawl_reports(max_pages: int = 3) -> list[dict]:
    """
    Firecrawl로 네이버 증권 종목분석 리포트 목록 수집.
    반환값: 리포트 메타데이터 dict 리스트
    """
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        raise ValueError("FIRECRAWL_API_KEY가 설정되지 않았습니다.")

    app = FirecrawlApp(api_key=api_key)
    reports: list[dict] = []

    for page_num in range(1, max_pages + 1):
        url = f"{BASE_URL}?page={page_num}"
        logger.info("크롤링 중: %s", url)
        try:
            result = app.scrape(
                url,
                formats=["markdown"],
                only_main_content=False,
            )
            markdown = result.markdown if hasattr(result, "markdown") else (result.get("markdown") or "")
        except Exception as e:
            logger.error("Firecrawl 오류 (page=%d): %s", page_num, e)
            continue

        page_reports = _parse_markdown_table(markdown)
        logger.info("  → %d건 파싱 (page=%d)", len(page_reports), page_num)
        reports.extend(page_reports)

        if page_num < max_pages:
            time.sleep(DELAY)

    logger.info("총 %d건 수집 완료", len(reports))
    return reports
