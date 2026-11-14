"""Load and verify the benchmark before any retrieval run.

Checks the split sizes and filter coverage that BENCHMARK.md requires, and that every
gold evidence quote really is on its gold page after normalisation. It also reports
the article [Điều] and clause [khoản] where each quote sits, to cross-check the
citations written in expected_answer.

Run from starter/:  python -m src.benchmark
"""
import csv
import json
import re
import sys
from typing import Any

from .corpus import REPO_ROOT
from .ingestion import load_corpus

QUESTIONS_PATH = REPO_ROOT / "benchmark" / "questions.json"
VERIFICATION_PATH = REPO_ROOT / "benchmark" / "verification.csv"
# Extra questions written after the main results, before any hybrid run (GUIDE optional extension).
HOLDOUT_PATH = REPO_ROOT / "benchmark" / "holdout.json"
HOLDOUT_VERIFICATION_PATH = REPO_ROOT / "benchmark" / "holdout_verification.csv"
FILTER_FIELDS = {"year"}

_ARTICLE = re.compile(r"^Điều (\d+)\.")
_CLAUSE = re.compile(r"^(\d{1,2})\.\s")


def squash(text: str) -> str:
    """Whitespace-insensitive form used for every evidence comparison."""
    return re.sub(r"\s+", " ", text).strip()


def load_questions(path=QUESTIONS_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def gold_entries(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Primary gold first, then every documented alternative; any one is a valid hit."""
    primary = {"source_id": question["gold_source_id"], "page": question["gold_page"],
               "evidence_quote": question["evidence_quote"]}
    return [primary, *question.get("alternative_gold", [])]


def _locate(page: Any, quote: str) -> str:
    """Article and clause in force where the quote starts on this page."""
    article, clause = page.metadata["article"], None
    position = squash(page.text).find(squash(quote))
    consumed = 0
    for block in page.text.splitlines():
        if consumed > position:
            break
        if m := _ARTICLE.match(block):
            article, clause = int(m.group(1)), None
        elif m := _CLAUSE.match(block):
            clause = int(m.group(1))
        consumed += len(squash(block)) + 1
    return f"Điều {article}" + (f" khoản {clause}" if clause else "")


def _check_gold(questions: list[dict], pages: dict, problems: list[str]) -> list[dict]:
    """Every gold quote must be on its content page and not copied into the question."""
    filter_values = {f: {p.metadata[f] for p in pages.values()} for f in FILTER_FIELDS}
    rows = []
    for q in questions:
        for field, value in (q.get("filter") or {}).items():
            if field not in FILTER_FIELDS or value not in filter_values[field]:
                problems.append(f"{q['id']}: filter {field}={value} not in corpus metadata")
        for gold in gold_entries(q):
            page = pages.get((gold["source_id"], gold["page"]))
            quote = gold["evidence_quote"]
            found = page is not None and page.metadata["status"] == "content" \
                and squash(quote) in squash(page.text)
            if squash(quote) in squash(q["question"]):
                problems.append(f"{q['id']}: evidence quote is copied into the question")
            if not found:
                problems.append(f"{q['id']}: quote not on {gold['source_id']} p{gold['page']}")
            rows.append({"id": q["id"], "split": q["split"], "type": q["type"],
                         "filter": json.dumps(q.get("filter"), ensure_ascii=False),
                         "source_id": gold["source_id"], "page": gold["page"],
                         "quote_chars": len(quote), "found_on_page": found,
                         "located_at": _locate(page, quote) if found else ""})
    return rows


def _write_rows(path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print_rows(rows: list[dict], problems: list[str], path) -> None:
    for r in rows:
        print(f"  {r['id']} {r['split']:7} {r['source_id']:13} p{r['page']:<3} "
              f"{'ok ' if r['found_on_page'] else 'MISSING'} {r['located_at']}")
    for p in problems:
        print("PROBLEM:", p)
    print("wrote", path.relative_to(REPO_ROOT).as_posix())


def verify() -> bool:
    questions = load_questions()
    pages = {(p.document_id, p.page): p for p in load_corpus()}
    problems: list[str] = []

    ids = [q["id"] for q in questions]
    if len(set(ids)) != len(ids):
        problems.append("duplicate question ids")
    by_split = {s: [q for q in questions if q["split"] == s] for s in ("dev", "test")}
    filtered = [q for q in questions if q.get("filter")]
    if len(questions) < 20:
        problems.append(f"only {len(questions)} questions, need >= 20")
    if len(by_split["dev"]) < 15 or len(by_split["test"]) < 5:
        problems.append("need >= 15 dev and >= 5 test")
    if len(filtered) < 3 or not any(q["split"] == "test" for q in filtered):
        problems.append("need >= 3 filter questions with >= 1 in test")

    rows = _check_gold(questions, pages, problems)
    _write_rows(VERIFICATION_PATH, rows)
    print(f"{len(questions)} questions: {len(by_split['dev'])} dev / {len(by_split['test'])} test; "
          f"{len(filtered)} with a filter ({sum(q['split'] == 'test' for q in filtered)} in test); "
          f"{sum(r['found_on_page'] for r in rows)}/{len(rows)} gold quotes found on their page")
    _print_rows(rows, problems, VERIFICATION_PATH)
    return not problems


def _article_key(row: dict) -> tuple[str, str]:
    """(law, 'Điều N') of a located gold quote, to detect articles shared between sets."""
    return row["source_id"][:7], row["located_at"].split(" khoản")[0]


def verify_holdout() -> bool:
    """Holdout: quotes on their pages, new ids, and no gold article shared with a dev question."""
    main, holdout = load_questions(), load_questions(HOLDOUT_PATH)
    pages = {(p.document_id, p.page): p for p in load_corpus()}
    problems: list[str] = []
    holdout_ids = [q["id"] for q in holdout]
    if len(set(holdout_ids)) != len(holdout_ids) or set(holdout_ids) & {q["id"] for q in main}:
        problems.append("holdout ids must be unique and new")
    if any(q["split"] != "holdout" for q in holdout):
        problems.append("every holdout question needs split = holdout")
    rows = _check_gold(holdout, pages, problems)
    dev = [q for q in main if q["split"] == "dev"]
    dev_articles = {_article_key(r) for r in _check_gold(dev, pages, [])}
    for r in rows:
        if _article_key(r) in dev_articles:
            problems.append(f"{r['id']}: shares {r['located_at']} ({r['source_id']}) with a dev question")
    _write_rows(HOLDOUT_VERIFICATION_PATH, rows)
    print(f"{len(holdout)} holdout questions; {sum(bool(q.get('filter')) for q in holdout)} with a filter; "
          f"{sum(r['found_on_page'] for r in rows)}/{len(rows)} gold quotes found on their page")
    _print_rows(rows, problems, HOLDOUT_VERIFICATION_PATH)
    return not problems


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    ok = verify_holdout() if "--holdout" in sys.argv[1:] else verify()
    sys.exit(0 if ok else 1)
