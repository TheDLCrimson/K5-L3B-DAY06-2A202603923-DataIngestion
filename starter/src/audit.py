"""Manual-audit support: metadata check, label summaries and a review sheet.

- Metadata: for each test question x top-3 x configuration, checks source, page range,
  year and article against the extracted page text (data/processed/pages.jsonl,
  produced by a separate code path from the chunker) and the manifest.
- Coherence and grounding: reads the hand labels in results/audit/*_labels.csv and
  summarises them; rows not yet confirmed by the author are reported as such.

Run from starter/:  python -m src.audit
"""

import csv
import json
import re
import statistics
import sys
from collections import Counter

from .benchmark import load_questions
from .corpus import MANIFEST_PATH, PAGES_PATH, REPO_ROOT

AUDIT = REPO_ROOT / "results" / "audit"
_ARTICLE = re.compile(r"(?m)^Điều (\d+)\.")


def _read_csv(name: str) -> list[dict]:
    with (AUDIT / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_pages() -> dict:
    pages = {}
    with PAGES_PATH.open(encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            pages[(p["document_id"], p["page"])] = p
    return pages


def _chunks() -> dict:
    out = {}
    for strategy in ("A", "B"):
        with (REPO_ROOT / "results" / "chunks" / f"{strategy}.jsonl").open(encoding="utf-8") as f:
            for line in f:
                c = json.loads(line)
                out[c["chunk_id"]] = c
    return out


def check_metadata() -> list[dict]:
    manifest = {d["source_id"]: d for d in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["documents"]}
    pages, chunks, rows = _load_pages(), _chunks(), []
    for config in ("A", "B", "C"):
        with (REPO_ROOT / "results" / "traces" / f"test_{config}.jsonl").open(encoding="utf-8") as f:
            traces = [json.loads(line) for line in f]
        for t in traces:
            for h in t["hits"]:
                chunk = chunks[h["chunk_id"]]
                body = chunk["text"].split("\n", 1)[1] if chunk["metadata"]["has_prefix"] else chunk["text"]
                doc = manifest[h["document_id"]]
                # The body must sit in the claimed pages, start on `page` and end on `page_end`.
                span = [pages[(h["document_id"], n)]["text"] for n in range(h["page"], h["page_end"] + 1)]
                joined = "\n".join(span)
                pos = joined.find(body)
                page_ok = pos >= 0 and pos < len(span[0]) and pos + len(body) > len(joined) - len(span[-1])
                # Article in force at the chunk start, read from the start page's own text.
                start_page = pages[(h["document_id"], h["page"])]
                before = [int(m.group(1)) for m in _ARTICLE.finditer(span[0]) if m.start() <= pos]
                expected_article = before[-1] if before else start_page["metadata"]["article"]
                row = {
                    "config": config,
                    "id": t["id"],
                    "rank": h["rank"],
                    "chunk_id": h["chunk_id"],
                    "document_id": h["document_id"],
                    "source": h["source"],
                    "page": h["page"],
                    "page_end": h["page_end"],
                    "year": h["year"],
                    "article": h["article"],
                    "source_ok": h["source"] == doc["local_path"].removeprefix("data/raw/"),
                    "page_ok": page_ok and pos >= 0,
                    "year_ok": h["year"] == doc["year"],
                    "article_ok": h["article"] == expected_article,
                    "gazette_pages": "-".join(
                        str(pages[(h["document_id"], n)]["metadata"]["gazette_page"])
                        for n in (h["page"], h["page_end"])
                    ),
                }
                row["complete_and_correct"] = all(
                    row[k] for k in ("source_ok", "page_ok", "year_ok", "article_ok")
                )
                rows.append(row)
    with (AUDIT / "metadata_check.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _label_summary(rows: list[dict]) -> dict:
    out = {}
    for strategy in sorted({r["strategy"] for r in rows}):
        labels = [int(r["label"]) for r in rows if r["strategy"] == strategy]
        counts = Counter(labels)
        out[strategy] = {
            "n": len(labels),
            "count_0": counts[0],
            "count_1": counts[1],
            "count_2": counts[2],
            "mean_of_2": round(statistics.mean(labels), 2),
            "unreviewed": sum(r["reviewed_by_author"] != "yes" for r in rows if r["strategy"] == strategy),
        }
    return out


def review_sheet(coherence: list[dict], grounding: list[dict]) -> None:
    chunks = _chunks()
    positions = json.loads((REPO_ROOT / "benchmark" / "coherence_positions.json").read_text(encoding="utf-8"))
    questions = {q["id"]: q for q in load_questions()}
    lines = [
        "# Audit review sheet",
        "",
        "Generated by `python -m src.audit`.",
        "Read each chunk, then set `label` and `reviewed_by_author=yes` in "
        "`coherence_labels.csv` and `grounding_labels.csv`.",
        "",
        "## Coherence (0 cut mid-idea or missing subject, 1 partly understandable, 2 self-contained)",
        "",
    ]
    for p in positions["positions"]:
        lines += [
            f"### {p['position_id']}: {p['document_id']} p{p['page']}, Điều {p['article']}",
            "",
            f'Position: middle of the clause "{p["clause_preview"]}..."',
            "",
        ]
        for strategy in ("A", "B"):
            label = next(
                r for r in coherence if r["position_id"] == p["position_id"] and r["strategy"] == strategy
            )
            lines += [
                f"**{strategy}** `{label['chunk_id']}`, draft label {label['label']}: {label['reason']}",
                "",
                "```text",
                chunks[label["chunk_id"]]["text"],
                "```",
                "",
            ]
    lines += [
        "## Grounding (top-3 together: 0 no evidence, 1 evidence missing a condition, "
        "2 enough to answer and trace)",
        "",
    ]
    for qid in sorted({r["id"] for r in grounding}):
        q = questions[qid]
        lines += [f"### {qid}: {q['question']}", "", f"Expected: {q['expected_answer']}", ""]
        for config in ("A", "B", "C"):
            label = next(r for r in grounding if r["id"] == qid and r["strategy"] == config)
            with (REPO_ROOT / "results" / "traces" / f"test_{config}.jsonl").open(encoding="utf-8") as f:
                trace = next(t for t in map(json.loads, f) if t["id"] == qid)
            lines += [f"**{config}**, draft label {label['label']}: {label['reason']}", ""]
            for h in trace["hits"]:
                lines += [
                    f"{h['rank']}. `{h['chunk_id']}` ({h['document_id']} p{h['page']}-{h['page_end']}, "
                    f"{h['year']}, Điều {h['article']}, score {h['score']}"
                    f"{', evidence hit' if h['evidence_hit'] else ''})",
                    "",
                ]
                if config != "C":
                    lines += ["```text", h["text"], "```", ""]
            if config == "C":
                lines += ["C searches B's chunks; texts are shown under B when the IDs match.", ""]
    (AUDIT / "review_sheet.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    meta = check_metadata()
    coherence, grounding = _read_csv("coherence_labels.csv"), _read_csv("grounding_labels.csv")
    metrics = json.loads((REPO_ROOT / "results" / "metrics_test.json").read_text(encoding="utf-8"))
    summary = {
        "metadata_complete_and_correct": {
            c: f"{sum(r['complete_and_correct'] for r in meta if r['config'] == c)}"
            f"/{sum(r['config'] == c for r in meta)}"
            for c in ("A", "B", "C")
        },
        "metadata_errors": [r for r in meta if not r["complete_and_correct"]],
        "filter_compliance_test": {c: metrics[c]["test"]["filter_compliance"] for c in ("A", "B", "C")},
        "coherence": _label_summary(coherence) | {"C": "same chunks as B, so the same labels"},
        "grounding": _label_summary(grounding),
    }
    (AUDIT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    review_sheet(coherence, grounding)
    print(
        json.dumps({k: v for k, v in summary.items() if k != "metadata_errors"}, ensure_ascii=False, indent=2)
    )
    print("metadata errors:", len(summary["metadata_errors"]))


if __name__ == "__main__":
    main()
