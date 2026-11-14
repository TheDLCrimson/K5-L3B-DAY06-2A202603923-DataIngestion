"""Build data/manifest.json, results/ingestion_log.csv and data/processed/pages.jsonl.

Run from starter/:  python -m src.manifest
"""
import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import asdict

import pymupdf

from .corpus import (
    ACCESSED_AT,
    INGESTION_LOG_PATH,
    LICENSE,
    MANIFEST_PATH,
    MIN_CONTENT_CHARS,
    PAGES_PATH,
    RAW_DIR,
    REPO_ROOT,
    SOURCES,
)
from .ingestion import load_corpus

PAGE_RULE = (
    f"A physical PDF page counts as a content page when, after removing the gazette running "
    f"header, the printed page number and '(Tiếp theo / Xem tiếp Công báo ...)' notes, its "
    f"NFC-normalised text has at least {MIN_CONTENT_CHARS} characters of law text. Excluded: "
    f"empty pages, pages under the threshold, and the publisher colophon. The files have no "
    f"cover, table of contents or reference pages."
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    pages = load_corpus()
    by_source = defaultdict(list)
    for page in pages:
        by_source[page.document_id].append(page)

    documents = []
    for source in SOURCES:
        src_pages = by_source[source.source_id]
        content = [p for p in src_pages if p.metadata["status"] == "content"]
        with pymupdf.open(RAW_DIR / source.file) as doc:
            physical = doc.page_count
        documents.append({
            "source_id": source.source_id,
            "title": source.title,
            "law_number": source.law_number,
            "year": source.year,
            "gazette": source.gazette,
            "articles": source.articles,
            "source_path_or_url": source.record_url,
            "vbpl_attachment": source.attachment,
            "local_path": f"data/raw/{source.file}",
            "sha256": _sha256(RAW_DIR / source.file),
            "license_or_permission": LICENSE,
            "accessed_at": ACCESSED_AT,
            "status_note": source.status_note,
            "physical_pages": physical,
            "content_pages": len(content),
            "content_page_ids": [p.page for p in content],
            "excluded_pages": {str(p.page): p.metadata["reason"] for p in src_pages
                               if p.metadata["status"] != "content"},
            "content_chars": sum(len(p.text) for p in content),
        })

    manifest = {
        "corpus": "Luật Đất đai 2013 (45/2013/QH13) and 2024 (31/2024/QH15), official Công báo text",
        "page_unit": "physical PDF page, 1-based, within each source file",
        "page_counting_rule": PAGE_RULE,
        "extraction": (f"PyMuPDF {pymupdf.VersionBind}, page.get_text(); Unicode NFC; "
                       "see starter/src/ingestion.py"),
        "total_physical_pages": sum(d["physical_pages"] for d in documents),
        "total_content_pages": sum(d["content_pages"] for d in documents),
        "total_content_chars": sum(d["content_chars"] for d in documents),
        "documents": documents,
    }
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    INGESTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with INGESTION_LOG_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["document_id", "page", "gazette_page", "status", "reason", "raw_chars",
                         "normalized_chars", "chapter_at_top", "article_at_top", "articles_starting",
                         "removed"])
        for p in pages:
            m = p.metadata
            writer.writerow([p.document_id, p.page, m["gazette_page"], m["status"], m["reason"],
                             m["raw_chars"], len(p.text), m["chapter"], m["article"],
                             " ".join(map(str, m["articles_starting"])), "; ".join(m["removed"])])

    PAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PAGES_PATH.open("w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")

    print(f"{manifest['total_content_pages']} content pages of {manifest['total_physical_pages']} "
          f"physical, {manifest['total_content_chars']:,} chars")
    for d in documents:
        print(f"  {d['source_id']}: {d['content_pages']}/{d['physical_pages']} pages, "
              f"{d['content_chars']:,} chars, excluded {d['excluded_pages']}")
    for path in (MANIFEST_PATH, INGESTION_LOG_PATH, PAGES_PATH):
        print("wrote", path.relative_to(REPO_ROOT).as_posix())


if __name__ == "__main__":
    main()
