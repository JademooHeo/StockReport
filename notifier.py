from pathlib import Path
"""텔레그램 봇으로 메시지 전송."""

import os
import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
MAX_LENGTH = 4096


def send(text: str) -> bool:
    """
    텔레그램으로 메시지 전송 (HTML 파싱 모드).
    4096자 초과 시 자동 분할.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 설정되지 않았습니다.")

    url = TELEGRAM_API.format(token=token, method="sendMessage")
    chunks = _split(text)

    for chunk in chunks:
        resp = httpx.post(url, json={
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        resp.raise_for_status()

    return True


def _split(text: str) -> list[str]:
    """메시지를 MAX_LENGTH 단위로 분할."""
    if len(text) <= MAX_LENGTH:
        return [text]
    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > MAX_LENGTH:
            chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks
