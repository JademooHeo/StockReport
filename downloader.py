"""PDF 다운로더 (httpx, 재시도 2회)."""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)
logger = logging.getLogger(__name__)

PDF_SAVE_DIR = Path(os.getenv("PDF_SAVE_DIR", "./pdfs"))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com",
    "Accept": "application/pdf,*/*",
}
MAX_RETRIES = 2
TIMEOUT = 30.0


def _safe_filename(text: str) -> str:
    return re.sub(r"[^\w가-힣-]", "_", text or "unknown")


def build_pdf_path(stock_code: str, published_at: str, securities_firm: str) -> Path:
    PDF_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{_safe_filename(stock_code)}_"
        f"{_safe_filename(published_at)}_"
        f"{_safe_filename(securities_firm)}.pdf"
    )
    return PDF_SAVE_DIR / filename


def download_pdf(
    pdf_url: str,
    save_path: Path,
    retries: int = MAX_RETRIES,
) -> Optional[Path]:
    """
    PDF를 save_path에 다운로드.
    성공 시 Path 반환, 실패 시 None 반환.
    """
    if save_path.exists() and save_path.stat().st_size > 1024:
        logger.info("  이미 존재: %s (스킵)", save_path.name)
        return save_path

    for attempt in range(1, retries + 2):  # +2 → 총 retries+1회 시도
        try:
            with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=TIMEOUT) as client:
                response = client.get(pdf_url)
                response.raise_for_status()

                content_type = response.headers.get("content-type", "")
                if "pdf" not in content_type.lower() and len(response.content) < 1024:
                    raise ValueError(f"PDF가 아닌 응답: {content_type}")

                save_path.write_bytes(response.content)
                logger.info("  다운로드 완료: %s (%d KB)", save_path.name, len(response.content) // 1024)
                return save_path

        except Exception as e:
            logger.warning("  다운로드 실패 (시도 %d/%d): %s | %s", attempt, retries + 1, pdf_url, e)
            if attempt <= retries:
                time.sleep(1.5 * attempt)

    logger.error("  최종 실패 (스킵): %s", pdf_url)
    return None
