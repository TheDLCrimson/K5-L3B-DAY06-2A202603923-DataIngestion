"""Optional extension (GUIDE.md): hybrid retrieval D = C's dense search + BM25, fused.

D keeps B's chunks and C's year filter. The dense side is pgvector (top 20, cosine);
the keyword side is BM25 over the same chunks (top 20), with the same filter. The
two lists are fused by reciprocal rank fusion or by a weighted sum of min-max
normalised scores; the option is chosen on dev only.

Run from starter/, in this order:
    python -m src.hybrid --stage dev    # grid on dev, writes results/hybrid/selection.json
    python -m src.hybrid --stage eval   # chosen D against C on dev, test (already seen) and holdout
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from rank_bm25 import BM25Okapi

from .benchmark import HOLDOUT_PATH, load_questions
from .corpus import REPO_ROOT
from .embeddings import E5Embedder
from .evaluation import evaluate_query
from .store import PgVectorStore

OUT = REPO_ROOT / "results" / "hybrid"
SELECTION_PATH = OUT / "selection.json"
CANDIDATES = 20  # per side, before fusion
TOP_K = 3
RRF_K = 60  # the usual constant; not tuned
# Grid, in tie-break order: on equal dev Hit@3 and Hit@1, the earlier option wins.
# RRF first (no weight to tune), then the weights from most dense to least.
GRID = [("rrf", None), ("weighted", 0.7), ("weighted", 0.5), ("weighted", 0.3)]


def tokenize(text: str) -> list[str]:
    """Lower-cased NFC syllables plus adjacent syllable pairs (Vietnamese words span syllables)."""
    syllables = re.findall(r"\w+", unicodedata.normalize("NFC", text).lower())
    return syllables + [f"{a}_{b}" for a, b in zip(syllables, syllables[1:])]


class Bm25Index:
    def __init__(self, chunks: list[dict]) -> None:
        self.chunks = chunks
        self.bm25 = BM25Okapi([tokenize(c["text"]) for c in chunks])  # default k1=1.5, b=0.75

    def search(self, question: str, top_k: int, filters: dict | None) -> list[tuple[dict, float]]:
        scores = self.bm25.get_scores(tokenize(question))
        allowed = [
            i
            for i, c in enumerate(self.chunks)
            if all(c["metadata"][k] == v for k, v in (filters or {}).items())
        ]
        ranked = sorted(allowed, key=lambda i: (-scores[i], self.chunks[i]["chunk_id"]))[:top_k]
        return [(self.chunks[i], float(scores[i])) for i in ranked]


def _hit_from_chunk(chunk: dict, score: float) -> dict:
    m = chunk["metadata"]
    return {
        "chunk_id": chunk["chunk_id"],
        "document_id": chunk["document_id"],
        "source": chunk["source"],
        "page": chunk["page"],
        "page_end": m["page_end"],
        "year": m["year"],
        "article": m["article"],
        "chapter": m["chapter"],
        "text": chunk["text"],
        "score": score,
    }


def _minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    low, high = min(scores.values()), max(scores.values())
    return {k: (v - low) / (high - low) if high > low else 1.0 for k, v in scores.items()}


def fuse(
    dense: list[dict],
    keyword: list[tuple[dict, float]],
    method: str,
    alpha: float | None,
    chunks_by_id: dict[str, dict],
) -> list[dict]:
    dense_rank = {h["chunk_id"]: r for r, h in enumerate(dense, start=1)}
    kw_rank = {c["chunk_id"]: r for r, (c, _) in enumerate(keyword, start=1)}
    ids = list(dict.fromkeys([*dense_rank, *kw_rank]))
    if method == "rrf":
        fused = {i: sum(1 / (RRF_K + ranks[i]) for ranks in (dense_rank, kw_rank) if i in ranks) for i in ids}
    else:
        d = _minmax({h["chunk_id"]: float(h["score"]) for h in dense})
        k = _minmax({c["chunk_id"]: s for c, s in keyword})
        fused = {i: alpha * d.get(i, 0.0) + (1 - alpha) * k.get(i, 0.0) for i in ids}
    best = sorted(ids, key=lambda i: (-fused[i], i))[:TOP_K]
    return [_hit_from_chunk(chunks_by_id[i], round(fused[i], 6)) for i in best]


def _option_name(method: str, alpha: float | None) -> str:
    return "rrf" if method == "rrf" else f"weighted_a{alpha}"


def _score(traces: list[dict]) -> dict:
    return {
        "n": len(traces),
        "hit_at_1": sum(t["hit_at_1"] for t in traces),
        "hit_at_3": sum(t["hit_at_3"] for t in traces),
    }


class Runner:
    def __init__(self) -> None:
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        self.embedder = E5Embedder()
        self.store = PgVectorStore(os.environ["DATABASE_URL"], self.embedder.dimensions)
        with (REPO_ROOT / "results" / "chunks" / "B.jsonl").open(encoding="utf-8") as f:
            chunks = [json.loads(line) for line in f]
        if self.store.count("B") != len(chunks):
            sys.exit("pgvector rows for B differ from results/chunks/B.jsonl; run python -m src.run first")
        self.chunks_by_id = {c["chunk_id"]: c for c in chunks}
        self.bm25 = Bm25Index(chunks)

    def candidates(self, q: dict) -> tuple[list[dict], list[tuple[dict, float]]]:
        filters = q.get("filter")  # C's rule: apply the question's filter when it has one
        dense = self.store.search("B", self.embedder.embed_query(q["question"]), CANDIDATES, filters)
        return dense, self.bm25.search(q["question"], CANDIDATES, filters)

    def run(self, questions: list[dict], options: list[tuple[str, float | None]]) -> dict[str, list[dict]]:
        traces: dict[str, list[dict]] = {"C": [], **{_option_name(*o): [] for o in options}}
        for q in questions:
            dense, keyword = self.candidates(q)
            traces["C"].append(evaluate_query(q, dense[:TOP_K], "C", q.get("filter")))
            for method, alpha in options:
                hits = fuse(dense, keyword, method, alpha, self.chunks_by_id)
                traces[_option_name(method, alpha)].append(evaluate_query(q, hits, "D", q.get("filter")))
        return traces


def _write(name: str, rows: list[dict]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def stage_dev(runner: Runner) -> None:
    if SELECTION_PATH.exists():
        sys.exit("results/hybrid/selection.json already exists; the dev choice is fixed")
    dev = [q for q in load_questions() if q["split"] == "dev"]
    traces = runner.run(dev, GRID)
    grid = {name: _score(ts) for name, ts in traces.items()}
    options = [_option_name(*o) for o in GRID]
    chosen = max(options, key=lambda o: (grid[o]["hit_at_3"], grid[o]["hit_at_1"], -options.index(o)))
    _write("grid_dev.jsonl", [t | {"option": name} for name, ts in traces.items() for t in ts])
    SELECTION_PATH.write_text(
        json.dumps(
            {
                "rule": "highest dev Evidence Hit@3, then Hit@1; remaining ties go to the earlier option in "
                "[rrf, weighted 0.7, 0.5, 0.3]",
                "dev_grid": grid,
                "chosen": chosen,
                "decided_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "note": "chosen before any hybrid run on test or holdout",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(grid, indent=1), "\nchosen:", chosen)


def stage_eval(runner: Runner) -> None:
    if not SELECTION_PATH.exists():
        sys.exit("No hybrid option chosen on dev yet; run --stage dev first")
    chosen = json.loads(SELECTION_PATH.read_text(encoding="utf-8"))["chosen"]
    method, alpha = next(o for o in GRID if _option_name(*o) == chosen)
    sets = {
        "dev": [q for q in load_questions() if q["split"] == "dev"],
        "test_already_seen": [q for q in load_questions() if q["split"] == "test"],
        "holdout": load_questions(HOLDOUT_PATH),
    }
    summary, changes = {"chosen": chosen}, []
    for name, questions in sets.items():
        traces = runner.run(questions, [(method, alpha)])
        c, d = traces["C"], traces[chosen]
        summary[name] = {"C": _score(c), "D": _score(d)}
        for tc, td in zip(c, d):
            rc, rd = tc["first_hit_rank"] or 99, td["first_hit_rank"] or 99
            if rc != rd:
                changes.append(
                    {
                        "set": name,
                        "id": tc["id"],
                        "filter": tc["question_filter"],
                        "C_first_hit_rank": tc["first_hit_rank"],
                        "D_first_hit_rank": td["first_hit_rank"],
                        "effect": "D better" if rd < rc else "D worse",
                        "C_top3": [h["chunk_id"] for h in tc["hits"]],
                        "D_top3": [h["chunk_id"] for h in td["hits"]],
                    }
                )
        _write(f"traces_{name}_C.jsonl", c)
        _write(f"traces_{name}_D.jsonl", d)
    summary["changes"] = changes
    (OUT / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for name in sets:
        s = summary[name]
        print(
            f"{name:18} C Hit@1 {s['C']['hit_at_1']}/{s['C']['n']} Hit@3 {s['C']['hit_at_3']}/{s['C']['n']}"
            f"  |  D Hit@1 {s['D']['hit_at_1']}/{s['D']['n']} Hit@3 {s['D']['hit_at_3']}/{s['D']['n']}"
        )
    for ch in changes:
        print(
            ch["set"],
            ch["id"],
            ch["effect"],
            "C rank",
            ch["C_first_hit_rank"],
            "-> D rank",
            ch["D_first_hit_rank"],
        )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["dev", "eval"], required=True)
    stage = parser.parse_args().stage
    runner = Runner()
    stage_dev(runner) if stage == "dev" else stage_eval(runner)


if __name__ == "__main__":
    main()
