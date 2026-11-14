"""Corpus definition: which files are in scope, where they came from, and the page rule.

Every value here is copied from the source document or its vbpl.vn record; nothing is inferred.
"""
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
MANIFEST_PATH = REPO_ROOT / "data" / "manifest.json"
PAGES_PATH = REPO_ROOT / "data" / "processed" / "pages.jsonl"
INGESTION_LOG_PATH = REPO_ROOT / "results" / "ingestion_log.csv"

ACCESSED_AT = "2026-11-14"

# Vietnamese IP Law 2005 (amended), Article 15(2): legal normative documents
# [văn bản quy phạm pháp luật] are not protected by copyright.
LICENSE = (
    "Not protected by copyright: legal normative document "
    "(Luật Sở hữu trí tuệ, Điều 15 khoản 2). Official gazette text, redistributable."
)

# A physical PDF page counts as a content page when, after removing the gazette
# running header, page number and continuation notes, it keeps at least this many
# characters of law text.
MIN_CONTENT_CHARS = 500


@dataclass(frozen=True)
class Source:
    source_id: str
    file: str
    title: str
    law_number: str
    year: int  # year the law was passed; the metadata filter field
    gazette: str
    record_url: str
    attachment: str
    articles: str  # article range covered by this file, read from the text
    status_note: str


_VBPL_2013 = "https://vbpl.vn/van-ban/chi-tiet/luat-dat-dai-so-45-2013-qh13--32833"
_VBPL_2024 = "https://vbpl.vn/van-ban/chi-tiet/luat-dat-dai-so-31-2024-qh15--177815"
_NOTE_2013 = (
    "vbpl.vn record: passed 29/11/2013, effective 01/07/2014, status 'Hết hiệu lực toàn bộ', "
    "expired 01/01/2025. Replaced by Luật Đất đai 31/2024/QH15 (Điều 252 khoản 4 of that law)."
)
_NOTE_2024 = (
    "Text as promulgated. Its Điều 252 sets effect from 01/01/2025 (Điều 190 and 248 from "
    "01/04/2024). vbpl.vn lists status 'Hết hiệu lực một phần': later amending laws exist and are "
    "NOT in this corpus, so gold answers quote this text only."
)

SOURCES: tuple[Source, ...] = (
    Source("ldd2013", "ldd2013_congbao-1011-1012.pdf", "Luật Đất đai 2013",
           "45/2013/QH13", 2013, "Công báo số 1011 + 1012, ngày 31-12-2013",
           _VBPL_2013, "VanBanGoc_45.2013.QH13.pdf", "Điều 1-212", _NOTE_2013),
    Source("ldd2024_cb363", "ldd2024_congbao-363-364.pdf", "Luật Đất đai 2024 (phần 1/3)",
           "31/2024/QH15", 2024, "Công báo số 363 + 364, ngày 01-3-2024",
           _VBPL_2024, "VanBanGoc_31-2024-qh15_1.pdf", "Điều 1-90", _NOTE_2024),
    Source("ldd2024_cb365", "ldd2024_congbao-365-366.pdf", "Luật Đất đai 2024 (phần 2/3)",
           "31/2024/QH15", 2024, "Công báo số 365 + 366, ngày 01-3-2024",
           _VBPL_2024, "VanBanGoc_31-2024-qh15_2.pdf", "Điều 91-170", _NOTE_2024),
    Source("ldd2024_cb367", "ldd2024_congbao-367-368.pdf", "Luật Đất đai 2024 (phần 3/3)",
           "31/2024/QH15", 2024, "Công báo số 367 + 368, ngày 01-3-2024",
           _VBPL_2024, "VanBanGoc_31-2024-qh15_3.pdf", "Điều 171-260", _NOTE_2024),
)
