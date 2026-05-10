from pathlib import Path
"""
Supabase 연동 모듈

-- Supabase 테이블 생성 SQL (프로젝트 SQL 에디터에서 실행) --

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS reports (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title        TEXT NOT NULL,
    stock_name   TEXT,
    stock_code   TEXT,
    securities_firm TEXT,
    analyst      TEXT,
    opinion      TEXT,
    target_price NUMERIC,
    published_at DATE,
    pdf_url      TEXT,
    source_url   TEXT UNIQUE NOT NULL,
    summary      JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_stock_code ON reports(stock_code);
CREATE INDEX IF NOT EXISTS idx_reports_published_at ON reports(published_at DESC);
"""

import logging
from typing import Optional
from supabase import create_client, Client
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent / ".env", override=True)
logger = logging.getLogger(__name__)


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_KEY"]
    return create_client(url, key)


def get_existing_urls(client: Client) -> set[str]:
    """DB에 이미 저장된 source_url 집합 반환."""
    result = client.table("reports").select("source_url").execute()
    return {row["source_url"] for row in result.data}


def upsert_report(client: Client, record: dict) -> bool:
    """
    reports 테이블에 upsert.
    source_url 충돌 시 summary, opinion, target_price만 갱신.
    반환값: True=신규, False=갱신
    """
    try:
        existing = (
            client.table("reports")
            .select("id")
            .eq("source_url", record["source_url"])
            .execute()
        )
        if existing.data:
            client.table("reports").update(
                {
                    "summary": record.get("summary"),
                    "opinion": record.get("opinion"),
                    "target_price": record.get("target_price"),
                }
            ).eq("source_url", record["source_url"]).execute()
            return False
        else:
            client.table("reports").insert(record).execute()
            return True
    except Exception as e:
        logger.error("DB upsert 실패 | %s | %s", record.get("source_url"), e)
        raise
