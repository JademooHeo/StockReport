from pathlib import Path
"""Claude API로 리포트 요약 생성 (claude-sonnet-4-6)."""

import json
import logging
import os
import re
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)
logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"

SYSTEM_PROMPT = """\
당신은 증권 애널리스트 리포트를 분석하는 전문가입니다.
리포트 본문을 읽고 아래 JSON만 반환하세요. 마크다운·설명 없이 순수 JSON만 출력하세요.

{
  "one_line": "50자 이내 핵심 한 줄 요약",
  "opinion": "BUY 또는 HOLD 또는 SELL (명시 없으면 null)",
  "target_price": 목표주가 숫자 또는 null,
  "key_points": ["핵심 논거 1", "핵심 논거 2", "핵심 논거 3"],
  "risks": ["리스크 1", "리스크 2"],
  "summary_quality": 1~5 정수 (텍스트 품질 자체 평가)
}"""


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    return anthropic.Anthropic(api_key=api_key)


def _extract_json(raw: str) -> dict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def summarize(pdf_text: str, title: str = "", stock_name: str = "") -> Optional[dict]:
    """Claude API로 요약. 반환값: dict or None."""
    header = f"[{title}] [{stock_name}]\n\n" if (title or stock_name) else ""
    user_content = f"{header}=== 리포트 본문 ===\n{pdf_text}"

    try:
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text
        result = _extract_json(raw)

        if isinstance(result.get("target_price"), str):
            try:
                result["target_price"] = float(result["target_price"].replace(",", ""))
            except (ValueError, AttributeError):
                result["target_price"] = None

        result["summary_quality"] = int(result.get("summary_quality") or 3)

        logger.info(
            "  요약 완료: %s | 캐시 hit=%s tokens",
            (title or "(제목없음)")[:30],
            getattr(response.usage, "cache_read_input_tokens", 0),
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("JSON 파싱 실패 | %s | %s", title, e)
        return None
    except Exception as e:
        logger.error("Claude API 오류 | %s | %s", title, e)
        return None
