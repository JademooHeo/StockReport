"""summary=null인 기존 레코드에 Claude 요약을 채워 넣는 일회성 스크립트."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import db
import extractor
import summarizer
from downloader import build_pdf_path

client = db.get_client()
# summary가 없거나 one_line이 비어있는 레코드 모두 재처리
all_rows = client.table("reports").select("*").execute().data
rows = [r for r in all_rows if not (r.get("summary") or {}).get("one_line")]
print(f"요약 필요 레코드: {len(rows)}건 (전체 {len(all_rows)}건 중)")

ok, fail = 0, 0
for row in rows:
    pdf_path = build_pdf_path(
        row.get("stock_code", "unknown"),
        str(row.get("published_at", "unknown")),
        row.get("securities_firm", "unknown"),
    )
    if not pdf_path.exists():
        print(f"  PDF 없음: {pdf_path.name}")
        fail += 1
        continue

    text = extractor.extract_text(pdf_path)
    if not text:
        fail += 1
        continue

    summary = summarizer.summarize(
        text,
        title=row.get("title", ""),
        stock_name=row.get("stock_name", ""),
    )
    if not summary:
        fail += 1
        continue

    client.table("reports").update({"summary": summary}).eq("id", row["id"]).execute()
    print(f"  완료: {row['stock_name']} - {row['title'][:30]}")
    ok += 1

print(f"\n성공: {ok}건 / 실패: {fail}건")
