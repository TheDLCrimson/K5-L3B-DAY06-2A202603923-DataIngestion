"""Score retrieved chunks against gold evidence and keep a full per-query trace.

A retrieved chunk is an evidence hit when it comes from a gold source, its page
range covers the gold page, and it contains the whole gold quote (any documented
alternative counts). Human labels for coherence and grounding are not inferred here.
"""
from typing import Any

from .benchmark import gold_entries
from .chunking import evidence_hit


def _as_chunk(hit: dict[str, Any]) -> dict[str, Any]:
    return {"document_id": hit["document_id"], "page": hit["page"], "text": hit["text"],
            "metadata": {"page_end": hit["page_end"]}}


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]], strategy: str,
                   applied_filter: dict[str, Any] | None) -> dict[str, Any]:
    golds = gold_entries(question)
    rows = []
    for rank, hit in enumerate(hits, start=1):
        chunk = _as_chunk(hit)
        rows.append({
            "rank": rank,
            "chunk_id": hit["chunk_id"],
            "score": round(float(hit["score"]), 4),
            "document_id": hit["document_id"],
            "source": hit["source"],
            "page": hit["page"],
            "page_end": hit["page_end"],
            "year": hit["year"],
            "article": hit["article"],
            "chapter": hit["chapter"],
            "evidence_hit": any(evidence_hit(chunk, g) for g in golds),
            "gold_page_match": any(hit["document_id"] == g["source_id"]
                                   and hit["page"] <= g["page"] <= hit["page_end"] for g in golds),
            "matches_filter": all(hit[k] == v for k, v in (applied_filter or {}).items()),
            # Whether the hit is from the version the question asks about, filtered or not.
            "matches_question_filter": all(hit[k] == v for k, v in (question.get("filter") or {}).items()),
            "text": hit["text"],
        })
    first = next((r["rank"] for r in rows if r["evidence_hit"]), None)
    return {
        "id": question["id"], "split": question["split"], "type": question["type"],
        "strategy": strategy, "question": question["question"],
        "question_filter": question.get("filter"), "applied_filter": applied_filter,
        "gold": golds, "hit_at_1": first == 1, "hit_at_3": first is not None and first <= 3,
        "first_hit_rank": first, "hits": rows,
    }


def _ratio(n: int, total: int) -> dict[str, Any]:
    return {"n": n, "N": total, "text": f"{n}/{total}", "rate": round(n / total, 3) if total else None}


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit@1 and Evidence Hit@3 by strategy and split, plus the filtered-question subset."""
    out: dict[str, Any] = {}
    for strategy in sorted({t["strategy"] for t in traces}):
        for split in sorted({t["split"] for t in traces}):
            ts = [t for t in traces if t["strategy"] == strategy and t["split"] == split]
            filtered = [t for t in ts if t["question_filter"]]
            applied = [t for t in ts if t["applied_filter"]]
            out.setdefault(strategy, {})[split] = {
                "hit_at_1": _ratio(sum(t["hit_at_1"] for t in ts), len(ts)),
                "evidence_hit_at_3": _ratio(sum(t["hit_at_3"] for t in ts), len(ts)),
                "filtered_questions_hit_at_3": _ratio(sum(t["hit_at_3"] for t in filtered), len(filtered)),
                "unfiltered_questions_hit_at_3": _ratio(
                    sum(t["hit_at_3"] for t in ts if not t["question_filter"]), len(ts) - len(filtered)),
                "filter_compliance": (_ratio(sum(all(h["matches_filter"] for h in t["hits"])
                                                 for t in applied), len(applied))
                                      if applied else "N/A: no filter applied"),
                "filtered_questions_top3_all_in_asked_version": _ratio(
                    sum(all(h["matches_question_filter"] for h in t["hits"]) for t in filtered),
                    len(filtered)),
            }
    return out
