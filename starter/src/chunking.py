"""Strategy A (fixed-size windows) and strategy B (article and clause aware) chunking.

Both strategies cut the same continuous text stream per source file, built from the
content pages in page order, so a chunk may span a page break and records its page
range. Chunking each page separately would cut every clause that runs over one of
the 370 page breaks, in both strategies, and hide the difference between them.

Run from starter/:  python -m src.chunking
"""
import bisect
import hashlib
import json
import random
import re
import statistics
import sys
from dataclasses import asdict, dataclass

from .benchmark import gold_entries, load_questions, squash
from .corpus import REPO_ROOT
from .ingestion import load_corpus
from .models import Chunk, DocumentPage

# Fixed before any retrieval run; see artifacts/experiment_log.csv.
A_SIZE, A_OVERLAP = 500, 50
B_MAX_SIZE = 1000

CHUNKS_DIR = REPO_ROOT / "results" / "chunks"
STATS_PATH = REPO_ROOT / "results" / "chunk_stats.json"
POSITIONS_PATH = REPO_ROOT / "benchmark" / "coherence_positions.json"
POSITIONS_SEED = 20261114
N_POSITIONS = 10

_CHAPTER = re.compile(r"^Chương ([IVXLC]+)\b")
_ARTICLE = re.compile(r"^Điều (\d+)\.")
_CLAUSE = re.compile(r"^\d{1,2}\.\s")
# Finer and finer boundaries for splitting an oversized clause: line (point [điểm]),
# sentence or clause punctuation, then any space.
_SPLIT_LEVELS = (re.compile(r"\n"), re.compile(r"(?<=[.;:])\s+"), re.compile(r" "))


@dataclass
class Stream:
    """One source file's content pages joined with newlines, with offset lookups."""
    document_id: str
    source: str
    year: int
    law_number: str
    text: str
    page_starts: list[int]  # stream offset where each page begins
    page_numbers: list[int]
    line_starts: list[int]  # stream offset of every line (block)
    lines: list[str]
    article_at: list[int | None]  # article in force at each line
    chapter_at: list[str | None]

    def page_of(self, offset: int) -> int:
        return self.page_numbers[bisect.bisect_right(self.page_starts, offset) - 1]

    def line_index(self, offset: int) -> int:
        return bisect.bisect_right(self.line_starts, offset) - 1

    def line_end(self, index: int) -> int:
        """Offset just past line `index`, without its newline."""
        return self.line_starts[index] + len(self.lines[index])

    def chunk(self, strategy: str, ordinal: int, start: int, end: int,
              prefix: str = "", unit: str = "window") -> Chunk:
        """Chunk over stream[start:end]; `prefix` is context text not copied from that span."""
        body = self.text[start:end]
        text = f"{prefix}\n{body}" if prefix else body
        first, last = self.line_index(start), self.line_index(max(start, end - 1))
        return Chunk(
            chunk_id=f"{strategy}-{self.document_id}-{ordinal:04d}",
            document_id=self.document_id,
            source=self.source,
            page=self.page_of(start),
            text=text,
            metadata={
                "strategy": strategy,
                "year": self.year,
                "law_number": self.law_number,
                "page_end": self.page_of(max(start, end - 1)),
                "article": self.article_at[first],
                "articles": sorted({a for a in self.article_at[first:last + 1] if a is not None}),
                "chapter": self.chapter_at[first],
                "unit": unit,
                "has_prefix": bool(prefix),
                "char_start": start,
                "char_end": end,
                "n_chars": len(text),
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            },
        )


def build_streams(pages: list[DocumentPage]) -> list[Stream]:
    """Join each source file's content pages; skipped pages stay in the ingestion log."""
    streams: list[Stream] = []
    for document_id in dict.fromkeys(p.document_id for p in pages):
        doc_pages = [p for p in pages if p.document_id == document_id
                     and p.metadata["status"] == "content"]
        first = doc_pages[0].metadata
        article, chapter = first["article"], first["chapter"]
        page_starts, line_starts, lines, article_at, chapter_at = [], [], [], [], []
        offset = 0
        for page in doc_pages:
            page_starts.append(offset)
            for line in page.text.splitlines():
                if m := _CHAPTER.match(line):
                    chapter = m.group(1)
                if m := _ARTICLE.match(line):
                    article = int(m.group(1))
                line_starts.append(offset)
                lines.append(line)
                article_at.append(article)
                chapter_at.append(chapter)
                offset += len(line) + 1
        streams.append(Stream(document_id, doc_pages[0].source, first["year"], first["law_number"],
                              "\n".join(p.text for p in doc_pages), page_starts,
                              [p.page for p in doc_pages], line_starts, lines, article_at, chapter_at))
    return streams


def fixed_size(stream: Stream, size: int = A_SIZE, overlap: int = A_OVERLAP) -> list[Chunk]:
    """Strategy A: character windows of `size` with `overlap`, blind to words and clauses."""
    if not 0 <= overlap < size:
        raise ValueError("overlap must be in [0, size)")
    chunks, start, step = [], 0, size - overlap
    while True:
        end = min(start + size, len(stream.text))
        chunks.append(stream.chunk("A", len(chunks), start, end))
        if end == len(stream.text):
            return chunks
        start += step


def _split_span(text: str, start: int, end: int, limit: int, level: int = 0) -> list[tuple[int, int]]:
    """Split text[start:end] into contiguous spans of at most `limit` characters.

    Cuts at the coarsest boundary level that works, packing pieces greedily, and
    falls back to a hard cut only when a single word run exceeds the limit.
    """
    if end - start <= limit:
        return [(start, end)]
    if level == len(_SPLIT_LEVELS):
        return [(s, min(s + limit, end)) for s in range(start, end, limit)]
    cuts = [m.end() for m in _SPLIT_LEVELS[level].finditer(text, start, end) if start < m.end() < end]
    bounds = [start, *cuts, end]
    atoms = [piece for a, b in zip(bounds, bounds[1:])
             for piece in _split_span(text, a, b, limit, level + 1)]
    packed: list[tuple[int, int]] = []
    for a, b in atoms:
        if packed and b - packed[-1][0] <= limit:
            packed[-1] = (packed[-1][0], b)
        else:
            packed.append((a, b))
    return packed


def _is_heading_line(line: str) -> bool:
    """Chapter, section or upper-case title lines that belong to the next article."""
    letters = [c for c in line if c.isalpha()]
    return line.startswith(("Chương ", "Mục ")) or (len(letters) >= 3 and all(c.isupper() for c in letters))


def _article_spans(stream: Stream) -> list[tuple[int, int, int | None]]:
    """(first line, end line, heading line) per article; headings above an article join it."""
    heading_lines = [i for i, line in enumerate(stream.lines) if _ARTICLE.match(line)]
    starts = []
    for i in heading_lines:
        j = i
        while j > 0 and _is_heading_line(stream.lines[j - 1]):
            j -= 1
        starts.append(j)
    if not starts or starts[0] != 0:  # preamble before the first article joins it
        starts = [0, *starts[1:]] if starts else [0]
    bounds = [*starts, len(stream.lines)]
    spans = []
    for first, end in zip(bounds, bounds[1:]):
        heading = next((i for i in range(first, end) if _ARTICLE.match(stream.lines[i])), None)
        spans.append((first, end, heading))
    return spans


def by_structure(stream: Stream, max_size: int = B_MAX_SIZE) -> list[Chunk]:
    """Strategy B: one chunk per article when it fits, else clause groups up to `max_size`.

    Chapter and section headings attach to the next article. Each later chunk of a
    long article repeats the article heading ("Điều 45. ...") as a prefix so every
    chunk says which article it belongs to; the prefix counts towards `max_size`.
    A clause longer than the budget is split at points, then sentences, then spaces.
    """
    chunks: list[Chunk] = []
    for first, end, heading_idx in _article_spans(stream):
        span_start, span_end = stream.line_starts[first], stream.line_end(end - 1)
        if span_end - span_start <= max_size:
            chunks.append(stream.chunk("B", len(chunks), span_start, span_end, unit="article"))
            continue
        heading = stream.lines[heading_idx] if heading_idx is not None else ""
        heading_end = stream.line_end(heading_idx) if heading_idx is not None else span_start

        def prefix_for(start: int) -> str:
            return heading if start > heading_end else ""

        def size(start: int, stop: int) -> int:
            p = prefix_for(start)
            return stop - start + (len(p) + 1 if p else 0)

        # Units: the opening (headings, article title, lead-in) together with clause 1,
        # so a heading never ends up alone in a chunk, then one unit per later clause.
        clause_lines = [i for i in range(first + 1, end)
                        if _CLAUSE.match(stream.lines[i]) and (heading_idx is None or i > heading_idx)]
        unit_lines = [first, *clause_lines[1:]]
        units = [(stream.line_starts[a], stream.line_end(b - 1))
                 for a, b in zip(unit_lines, [*unit_lines[1:], end])]
        current: tuple[int, int, str] | None = None
        for u_start, u_end in units:
            if current and size(current[0], u_end) <= max_size:
                current = (current[0], u_end, "clauses")
                continue
            if current:
                chunks.append(stream.chunk("B", len(chunks), current[0], current[1],
                                           prefix_for(current[0]), current[2]))
            if size(u_start, u_end) <= max_size:
                current = (u_start, u_end, "clauses")
                continue
            budget = max_size - len(heading) - 1
            pieces = _split_span(stream.text, u_start, u_end, budget)
            if len(pieces) > 1 and pieces[0][1] <= heading_end + 1:
                # The first piece is only the heading: give it the start of the clause.
                head = pieces[0]
                first_body = _split_span(stream.text, pieces[1][0], u_end, budget - (head[1] - head[0]))[0]
                pieces = [(head[0], first_body[1]),
                          *_split_span(stream.text, first_body[1], u_end, budget)]
            for a, b in pieces[:-1]:
                b = a + len(stream.text[a:b].rstrip())
                chunks.append(stream.chunk("B", len(chunks), a, b, prefix_for(a), "split"))
            current = (*pieces[-1], "split")
        if current:
            chunks.append(stream.chunk("B", len(chunks), current[0], current[1],
                                       prefix_for(current[0]), current[2]))
    return chunks


def build_chunks(streams: list[Stream] | None = None) -> dict[str, list[Chunk]]:
    """Chunks for A and B from the current corpus. C reuses B's chunks."""
    streams = streams if streams is not None else build_streams(load_corpus())
    return {"A": [c for s in streams for c in fixed_size(s)],
            "B": [c for s in streams for c in by_structure(s)]}


def evidence_hit(chunk: Chunk | dict, gold: dict) -> bool:
    """A chunk holds the evidence when it is from the gold page range and contains the full quote."""
    c = chunk if isinstance(chunk, dict) else asdict(chunk)
    meta = c["metadata"]
    return (c["document_id"] == gold["source_id"]
            and c["page"] <= gold["page"] <= meta["page_end"]
            and squash(gold["evidence_quote"]) in squash(c["text"]))


def _stats(chunks: list[Chunk]) -> dict:
    lengths = sorted(c.metadata["n_chars"] for c in chunks)
    q = statistics.quantiles(lengths, n=10)
    return {
        "chunks": len(chunks),
        "total_chars": sum(lengths),
        "mean_chars": round(statistics.mean(lengths), 1),
        "min": lengths[0], "p10": round(q[0]), "median": statistics.median(lengths),
        "p90": round(q[-1]), "max": lengths[-1],
        "spanning_a_page_break": sum(c.page != c.metadata["page_end"] for c in chunks),
        "by_unit": {u: sum(c.metadata["unit"] == u for c in chunks)
                    for u in sorted({c.metadata["unit"] for c in chunks})},
        "with_heading_prefix": sum(c.metadata["has_prefix"] for c in chunks),
    }


def _check_coverage(streams: list[Stream], chunks: list[Chunk], strategy: str) -> None:
    """Every non-space character of every stream must be inside some chunk body."""
    for stream in streams:
        covered = bytearray(len(stream.text))
        for c in chunks:
            if c.document_id == stream.document_id:
                covered[c.metadata["char_start"]:c.metadata["char_end"]] = b"\x01" * (
                    c.metadata["char_end"] - c.metadata["char_start"])
        missing = [i for i, ch in enumerate(stream.text) if not covered[i] and not ch.isspace()]
        if missing:
            raise AssertionError(f"{strategy} misses {len(missing)} chars of {stream.document_id}")
        if strategy == "B":  # B partitions the stream: chunk bodies never overlap
            spans = sorted((c.metadata["char_start"], c.metadata["char_end"]) for c in chunks
                           if c.document_id == stream.document_id)
            if any(b > c for (_, b), (c, _) in zip(spans, spans[1:])):
                raise AssertionError(f"B chunks overlap in {stream.document_id}")


def _coherence_positions(streams: list[Stream]) -> list[dict]:
    """10 clause midpoints sampled with a fixed seed, before any score is seen."""
    clauses = [(s, i) for s in streams for i, line in enumerate(s.lines) if _CLAUSE.match(line)]
    picked = sorted(random.Random(POSITIONS_SEED).sample(clauses, N_POSITIONS),
                    key=lambda si: (si[0].document_id, si[1]))
    positions = []
    for n, (stream, i) in enumerate(picked, start=1):
        offset = stream.line_starts[i] + len(stream.lines[i]) // 2
        positions.append({"position_id": f"P{n:02d}", "document_id": stream.document_id,
                          "page": stream.page_of(offset), "article": stream.article_at[i],
                          "char_offset": offset, "clause_preview": stream.lines[i][:100]})
    return positions


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    streams = build_streams(load_corpus())
    strategies = build_chunks(streams)
    for name, chunks in strategies.items():
        _check_coverage(streams, chunks, name)
        if len({c.chunk_id for c in chunks}) != len(chunks):
            raise AssertionError(f"{name} has duplicate chunk ids")

    questions = load_questions()
    stats = {"parameters": {"A": {"size": A_SIZE, "overlap": A_OVERLAP},
                            "B": {"max_size": B_MAX_SIZE, "unit": "article, else clause groups"}}}
    for name, chunks in strategies.items():
        reachable = {q["id"]: any(evidence_hit(c, g) for g in gold_entries(q) for c in chunks)
                     for q in questions}
        by_split = {s: [q["id"] for q in questions if q["split"] == s] for s in ("dev", "test")}
        stats[name] = _stats(chunks) | {
            "evidence_reachable": {s: f"{sum(reachable[i] for i in ids)}/{len(ids)}"
                                   for s, ids in by_split.items()},
            "evidence_unreachable_ids": [k for k, v in reachable.items() if not v],
        }
        CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
        with (CHUNKS_DIR / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")

    positions = _coherence_positions(streams)
    for p in positions:
        for name, chunks in strategies.items():
            p[f"chunk_{name}"] = next(
                c.chunk_id for c in chunks if c.document_id == p["document_id"]
                and c.metadata["char_start"] <= p["char_offset"] < c.metadata["char_end"])
    POSITIONS_PATH.write_text(json.dumps({"seed": POSITIONS_SEED, "rule": (
        "Midpoint of 10 clause [khoản] lines sampled with the fixed seed from all clause lines; "
        "for each strategy, the first chunk whose span contains the midpoint is labelled."),
        "positions": positions}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    for p in positions:
        print(p["position_id"], p["document_id"], f"p{p['page']}", f"Điều {p['article']}",
              p["chunk_A"], p["chunk_B"], "|", p["clause_preview"][:60])


if __name__ == "__main__":
    main()
