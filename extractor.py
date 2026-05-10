"""pdfplumber로 PDF 텍스트 추출."""

import logging
from pathlib import Path
from typing import Optional

import pdfplumber

logger = logging.getLogger(__name__)

MAX_CHARS = 5_000  # 로컬 LLM 품질 최적화: 핵심 내용은 앞 5,000자에 집중


def extract_text(pdf_path: Path) -> Optional[str]:
    """
    PDF에서 텍스트를 추출.
    MAX_CHARS 초과 시 앞부분만 사용 (핵심 내용은 보통 앞에 집중).
    반환값: 추출된 텍스트 or None (실패 시)
    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages_text = []
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)

        full_text = "\n\n".join(pages_text).strip()

        if not full_text:
            logger.warning("텍스트 추출 결과 없음: %s", pdf_path.name)
            return None

        if len(full_text) > MAX_CHARS:
            logger.debug(
                "텍스트 truncate %d → %d chars: %s",
                len(full_text), MAX_CHARS, pdf_path.name,
            )
            full_text = full_text[:MAX_CHARS]

        return full_text

    except Exception as e:
        logger.error("PDF 텍스트 추출 실패 %s: %s", pdf_path.name, e)
        return None
