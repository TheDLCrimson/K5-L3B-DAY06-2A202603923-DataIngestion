"""Day 06 pipeline: corpus -> chunks A/B -> e5 embeddings -> pgvector -> top-3 for A, B, C.

Run from starter/, dev first, then test once the configuration is chosen:
    python -m src.run --split dev
    python -m src.run --split test

The dev run applies the selection rule from benchmark/README.md and writes
results/selection.json; the test run refuses to start until that file holds a choice,
so no test result can influence it.
"""
import argparse
import hashlib
import json
import os
import platform
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .benchmark import load_questions
from .chunking import A_OVERLAP, A_SIZE, B_MAX_SIZE, build_chunks
from .corpus import REPO_ROOT
from .embeddings import MODEL_NAME, E5Embedder
from .evaluation import evaluate_query, summarize
from .store import PgVectorStore

RESULTS = REPO_ROOT / "results"
SELECTION_PATH = RESULTS / "selection.json"
# Configuration -> (stored rows it searches, whether the question's filter is applied)
CONFIGS = {"A": ("A", False), "B": ("B", False), "C": ("B", True)}
TOP_K = 3


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ingest(store: PgVectorStore, embedder: E5Embedder, chunks: dict) -> dict:
    """Sync A and B into pgvector, then sync again to prove the reload writes nothing."""
    log = {}
    for strategy, items in chunks.items():
        embed_seconds = 0.0

        def timed_embed(texts: list[str]) -> list[list[float]]:
            nonlocal embed_seconds
            start = time.perf_counter()
            vectors = embedder.embed_documents(texts)
            embed_seconds += time.perf_counter() - start
            return vectors

        start = time.perf_counter()
        first = store.sync(strategy, items, timed_embed, embedder.model_name)
        first_seconds = time.perf_counter() - start
        start = time.perf_counter()
        reload = store.sync(strategy, items, timed_embed, embedder.model_name)
        log[strategy] = {"sync": first, "sync_seconds": round(first_seconds, 2),
                         "embed_seconds": round(embed_seconds, 2),
                         "reload": reload, "reload_seconds": round(time.perf_counter() - start, 2),
                         "reload_wrote_nothing": reload["inserted"] == reload["updated"]
                         == reload["deleted"] == 0 and reload["rows_after"] == len(items)}
    return log


def change_check(store: PgVectorStore, embedder: E5Embedder, chunks: list) -> dict:
    """Simulate a changed source on a scratch strategy: one edited chunk, one removed."""
    name, sample = "_reload_check", chunks[:5]
    store.delete(name, list(store.existing(name)))
    steps = {"initial": store.sync(name, sample, embedder.embed_documents, embedder.model_name),
             "same_again": store.sync(name, sample, embedder.embed_documents, embedder.model_name)}
    edited_text = sample[0].text + " (sửa đổi)"
    edited = replace(sample[0], text=edited_text, metadata=sample[0].metadata | {
        "content_hash": hashlib.sha256(edited_text.encode("utf-8")).hexdigest()[:16]})
    steps["one_edited_one_removed"] = store.sync(name, [edited, *sample[1:4]],
                                                 embedder.embed_documents, embedder.model_name)
    store.delete(name, list(store.existing(name)))
    return steps


def run_queries(store: PgVectorStore, embedder: E5Embedder, questions: list[dict]) -> tuple[list, dict]:
    traces, embed_ms, search_ms = [], [], []
    for q in questions:
        start = time.perf_counter()
        vector = embedder.embed_query(q["question"])
        embed_ms.append((time.perf_counter() - start) * 1000)
        for config, (rows, use_filter) in CONFIGS.items():
            applied = q.get("filter") if use_filter else None
            start = time.perf_counter()
            hits = store.search(rows, vector, TOP_K, applied)
            search_ms.append((time.perf_counter() - start) * 1000)
            traces.append(evaluate_query(q, hits, config, applied))
    return traces, {"query_embed_ms_median": round(statistics.median(embed_ms), 1),
                    "search_ms_median": round(statistics.median(search_ms), 1)}


def select(metrics: dict) -> dict:
    """Selection rule from benchmark/README.md, steps 1-2; step 3 needs coherence labels."""
    dev = {c: metrics[c]["dev"] for c in CONFIGS}
    h3 = {c: dev[c]["evidence_hit_at_3"]["n"] for c in dev}
    h1 = {c: dev[c]["hit_at_1"]["n"] for c in dev}
    band1 = [c for c in dev if max(h3.values()) - h3[c] <= 1]
    decision = {"rule": "1) dev Evidence Hit@3, 2) dev Hit@1 (ties = within 1/15), "
                        "3) mean coherence, 4) fewer chunks and lower ingest time",
                "dev_hit_at_3": h3, "dev_hit_at_1": h1, "decided_at": _now()}
    if len(band1) == 1:
        return decision | {"chosen": band1[0], "decided_by_step": 1}
    band2 = [c for c in band1 if max(h1[x] for x in band1) - h1[c] <= 1]
    if len(band2) == 1:
        return decision | {"chosen": band2[0], "decided_by_step": 2, "tied_at_step_1": band1}
    return decision | {"chosen": None, "pending_step": 3, "tied_after_step_2": band2,
                       "next": "label coherence on benchmark/coherence_positions.json, then record "
                               "the choice here before running --split test"}


def environment(store: PgVectorStore, embedder: E5Embedder) -> dict:
    import psycopg
    import pymupdf
    import sentence_transformers
    import torch
    import transformers

    with store.connect() as conn:
        pg, vec = conn.execute("SELECT current_setting('server_version'), extversion "
                               "FROM pg_extension WHERE extname = 'vector'").fetchone()
    return {"python": platform.python_version(), "os": platform.platform(),
            "torch": torch.__version__, "sentence_transformers": sentence_transformers.__version__,
            "transformers": transformers.__version__, "psycopg": psycopg.__version__,
            "pymupdf": pymupdf.VersionBind, "postgresql": pg, "pgvector": vec,
            "device": embedder.device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "embedding_model": embedder.model_name, "model_revision": embedder.revision,
            "dimensions": embedder.dimensions, "max_tokens": embedder.max_tokens,
            "metric": "cosine (pgvector <=>), exact search, no ANN index",
            "cost_usd": 0, "cost_note": "local model and local database; electricity not measured"}


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    split = parser.parse_args().split
    if split == "test":
        selection = json.loads(SELECTION_PATH.read_text(encoding="utf-8")) if SELECTION_PATH.exists() else {}
        if not selection.get("chosen"):
            sys.exit("No configuration chosen on dev yet (results/selection.json); refusing to run test.")

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if os.getenv("EMBEDDING_MODEL", MODEL_NAME) != MODEL_NAME:
        sys.exit(f"EMBEDDING_MODEL must be {MODEL_NAME}: all strategies share one model")
    database_url = os.environ["DATABASE_URL"]

    started = _now()
    start = time.perf_counter()
    chunks = build_chunks()
    chunk_seconds = time.perf_counter() - start
    embedder = E5Embedder()
    store = PgVectorStore(database_url, embedder.dimensions)
    store.create_schema()
    ingest_log = ingest(store, embedder, chunks)
    reload_check = change_check(store, embedder, chunks["B"]) if split == "dev" else None

    questions = [q for q in load_questions() if q["split"] == split]
    traces, latency = run_queries(store, embedder, questions)
    for config in CONFIGS:
        _write_jsonl(RESULTS / "traces" / f"{split}_{config}.jsonl",
                     [t for t in traces if t["strategy"] == config])
    metrics = summarize(traces)
    (RESULTS / f"metrics_{split}.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if split == "dev":
        recorded = json.loads(SELECTION_PATH.read_text(encoding="utf-8")) if SELECTION_PATH.exists() else {}
        if recorded.get("chosen"):  # never overwrite a recorded decision on a dev re-run
            print("results/selection.json already holds a decision; left unchanged")
        else:
            SELECTION_PATH.write_text(json.dumps(select(metrics), ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")

    run_info = {"split": split, "started_at": started, "finished_at": _now(),
                "parameters": {"A": {"size": A_SIZE, "overlap": A_OVERLAP},
                               "B": {"max_size": B_MAX_SIZE}, "C": "B rows + year filter", "top_k": TOP_K},
                "chunk_counts": {s: len(c) for s, c in chunks.items()},
                "chunking_seconds": round(chunk_seconds, 2), "ingest": ingest_log,
                "change_check": reload_check, "query_latency": latency,
                "environment": environment(store, embedder)}
    (RESULTS / f"run_info_{split}.json").write_text(
        json.dumps(run_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for config in CONFIGS:
        m = metrics[config][split]
        print(f"{config} {split}: Hit@1 {m['hit_at_1']['text']}  "
              f"Evidence Hit@3 {m['evidence_hit_at_3']['text']}  "
              f"filtered Hit@3 {m['filtered_questions_hit_at_3']['text']}  "
              f"filtered top-3 in asked version {m['filtered_questions_top3_all_in_asked_version']['text']}")
    for strategy, log in ingest_log.items():
        print(f"ingest {strategy}: {log['sync']}  {log['sync_seconds']}s (embed {log['embed_seconds']}s); "
              f"reload {log['reload']} wrote_nothing={log['reload_wrote_nothing']}")
    if reload_check:
        print("change check:", reload_check)
    if split == "dev":
        print("selection:", json.loads(SELECTION_PATH.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
