"""Read and normalise the gazette PDFs, one DocumentPage per physical page.

PyMuPDF is used instead of pypdf: on these Công báo files pypdf splits Vietnamese
syllables ("Nguyên t ắc b ồi th ường"), while PyMuPDF returns them intact.
"""
import re
import unicodedata
from pathlib import Path

import pymupdf

from .corpus import MIN_CONTENT_CHARS, RAW_DIR, SOURCES, Source
from .models import DocumentPage

# Gazette furniture that is not law text.
_RUNNING_HEADER = re.compile(r"^CÔNG BÁO/Số [\d +]+/Ngày \d{2}-\d{1,2}-\d{4}$")
_PAGE_NUMBER = re.compile(r"^\d{1,4}$")
_CONTINUED_FROM = re.compile(r"^\(Tiếp theo Công báo số [\d +]+\)$")
_CONTINUED_IN = re.compile(r"^\(Xem tiếp Công báo số [\d +]+\)$")
_COLOPHON = "VĂN PHÒNG CHÍNH PHỦ XUẤT BẢN"

# Lines that open a new structural block in a Vietnamese law.
_NEW_BLOCK = re.compile(
    r"^(Chương [IVXLC]+\b|Mục \d+\b|Điều \d+\.|\d{1,2}\.\s|[a-zđ]\d?\)\s|[-+]\s"
    r"|Căn cứ |Luật này (đã )?được Quốc hội)"
)
_CHAPTER = re.compile(r"^Chương ([IVXLC]+)\b")
_ARTICLE = re.compile(r"^Điều (\d+)\.")

_SPACE_LIKE = dict.fromkeys(map(ord, "     \t"), " ")
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍﻿"), None)


def _is_title_case_line(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters)


def _is_article_heading(block: str) -> bool:
    """'Điều 3. Giải thích từ ngữ': an article number and title, no sentence yet."""
    m = _ARTICLE.match(block)
    return bool(m) and not re.search(r"[.;:]", block[m.end():])


def normalize_text(text: str) -> str:
    """NFC, unify spaces, and re-join hard-wrapped lines into structural blocks.

    One output line per block: chapter, section, article title, clause [khoản],
    point [điểm] or an upper-case title. Wrapped continuation lines are joined
    with a space, so the clause structure survives for structure-aware chunking.
    """
    text = unicodedata.normalize("NFC", text).translate(_SPACE_LIKE).translate(_INVISIBLE)
    blocks: list[str] = []
    prev_title = False
    for raw in text.splitlines():
        line = re.sub(r" {2,}", " ", raw).strip()
        if not line:
            continue
        title = _is_title_case_line(line)
        # A block starts at a structural marker or where upper-case title text
        # begins or ends; consecutive upper-case lines are one wrapped title.
        starts_block = not blocks or bool(_NEW_BLOCK.match(line)) or title != prev_title
        # An article heading is continued only by a wrapped title (lower-case start);
        # an upper-case start is the article's first sentence.
        if blocks and _is_article_heading(blocks[-1]) and line[:1].isupper():
            starts_block = True
        if starts_block:
            blocks.append(line)
        else:
            blocks[-1] = f"{blocks[-1]} {line}"
        prev_title = title
    return "\n".join(blocks)


def _strip_gazette_furniture(text: str) -> tuple[str, int | None, list[str]]:
    """Remove running header, printed page number and continuation notes.

    Returns the remaining text, the printed gazette page number and what was removed.
    """
    lines = [stripped for raw in text.splitlines() if (stripped := raw.strip())]
    removed: list[str] = []
    gazette_page = None
    kept: list[str] = []
    for position, line in enumerate(lines):
        if _CONTINUED_FROM.match(line):
            # A continuation issue opens with gazette section headings up to "(Tiếp theo ...)".
            removed.append("continuation preamble")
            kept = []
            continue
        if _RUNNING_HEADER.match(line):
            removed.append("running header")
            continue
        if position < 3 and gazette_page is None and _PAGE_NUMBER.match(line):
            gazette_page = int(line)
            removed.append("page number")
            continue
        if _CONTINUED_IN.match(line):
            removed.append("continued-in note")
            continue
        kept.append(line)
    return "\n".join(kept), gazette_page, removed


def load_pdf(path: Path, source: Source, chapter: str | None = None,
             article: int | None = None) -> list[DocumentPage]:
    """Extract every physical page, keeping page number, source and a status.

    Status is one of: content, short, empty, colophon, error. Pages are never
    dropped here; the caller decides what to index and the log records why.
    `chapter` and `article` carry the structure over from the previous gazette
    issue when one law is split across several files.
    """
    pages: list[DocumentPage] = []
    with pymupdf.open(path) as doc:
        for index, pdf_page in enumerate(doc, start=1):
            meta = {
                "law_number": source.law_number,
                "year": source.year,
                "title": source.title,
                "gazette_page": None,
                "chapter": chapter,  # chapter in force at the top of the page
                "article": article,  # article in force at the top of the page
                "articles_starting": [],
                "raw_chars": 0,
                "removed": [],
            }
            try:
                raw = pdf_page.get_text()
            except Exception as exc:  # a broken page must be logged, not lost
                meta.update(status="error", reason=f"{type(exc).__name__}: {exc}")
                pages.append(DocumentPage(source.source_id, source.file, index, "", meta))
                continue
            meta["raw_chars"] = len(raw)
            body, gazette_page, removed = _strip_gazette_furniture(raw)
            text = normalize_text(body)
            meta["gazette_page"] = gazette_page
            meta["removed"] = removed
            for block in text.splitlines():
                if m := _CHAPTER.match(block):
                    chapter = m.group(1)
                if m := _ARTICLE.match(block):
                    article = int(m.group(1))
                    meta["articles_starting"].append(article)
            meta["chapter_at_end"] = chapter
            meta["article_at_end"] = article
            if _COLOPHON in text:
                status, reason = "colophon", "publisher colophon of the gazette issue"
            elif not text:
                status, reason = "empty", "no text after removing gazette header"
            elif len(text) < MIN_CONTENT_CHARS:
                status, reason = "short", f"{len(text)} chars < {MIN_CONTENT_CHARS}"
            else:
                status, reason = "content", ""
            meta.update(status=status, reason=reason)
            pages.append(DocumentPage(source.source_id, source.file, index, text, meta))
    return pages


def load_corpus(raw_dir: Path = RAW_DIR) -> list[DocumentPage]:
    """Load every source in corpus.SOURCES, in order."""
    pages: list[DocumentPage] = []
    for source in SOURCES:
        path = raw_dir / source.file
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing; see data/manifest.json for where to get it")
        chapter = article = None
        if pages and pages[-1].metadata["law_number"] == source.law_number:
            chapter = pages[-1].metadata["chapter_at_end"]
            article = pages[-1].metadata["article_at_end"]
        pages.extend(load_pdf(path, source, chapter, article))
    return pages
