"""Compare pypdf and PyMuPDF on the corpus: how often does each split a syllable?

Vietnamese words are space-separated syllables, so a split shows up as a stray
single lower-case letter ("Nguyên t ắc"). Legitimate one-letter words (à, ạ, ở, ý, y)
are excluded, as are list markers like "a)". The count is an upper bound: on
PyMuPDF output the remaining hits are real words, mostly point references
("điểm a", "điểm b") and "ô nhiễm".

Run from starter/:  python -m src.extraction_check
"""
import csv
import re
import unicodedata

import pymupdf
import pypdf

from .corpus import RAW_DIR, REPO_ROOT, SOURCES

OUT = REPO_ROOT / "results" / "extraction_check.csv"
_LEGIT_SINGLE = set("àạởýy")


def _split_rate(text: str) -> tuple[int, int]:
    tokens = unicodedata.normalize("NFC", text).split()
    stray = [t for t in tokens if re.fullmatch(r"[^\W\d_]", t) and t.islower() and t not in _LEGIT_SINGLE]
    return len(stray), len(tokens)


def main() -> None:
    rows = []
    for source in SOURCES:
        path = RAW_DIR / source.file
        reader = pypdf.PdfReader(path)
        with pymupdf.open(path) as doc:
            for name, texts in (("pypdf", [p.extract_text() or "" for p in reader.pages]),
                                ("pymupdf", [p.get_text() for p in doc])):
                stray, tokens = map(sum, zip(*(_split_rate(t) for t in texts)))
                rows.append({"document_id": source.source_id, "extractor": name,
                             "version": pypdf.__version__ if name == "pypdf" else pymupdf.VersionBind,
                             "tokens": tokens, "stray_single_letters": stray,
                             "per_1000_tokens": round(1000 * stray / tokens, 2)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for r in rows:
        print(f"{r['document_id']:15} {r['extractor']:8} {r['stray_single_letters']:6} stray / "
              f"{r['tokens']:7} tokens = {r['per_1000_tokens']:6} per 1000")
    print("wrote", OUT.relative_to(REPO_ROOT).as_posix())


if __name__ == "__main__":
    main()
