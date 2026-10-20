"""Permission-filtered vector/lexical candidates fused by reciprocal ranks."""

import re

from sqlalchemy import case, func, literal, select

from app.reranking import rerank


def lexical_terms(question):
    # PostgreSQL simple FTS treats Chinese sentences as a token, so use bounded
    # Chinese bigrams and Latin identifiers. All values are SQL bind parameters.
    terms = re.findall(r"[a-z0-9_]+", question.casefold())
    for segment in re.findall(r"[\u4e00-\u9fff]+", question):
        terms.extend(segment[i : i + 2] for i in range(len(segment) - 1))
    return sorted(set(terms))[:64]


def select_candidates(session, allowed, vector, question, settings, make_citation):
    distance = allowed.c.embedding.cosine_distance(vector)
    size = settings.rag_top_k if settings.rag_strategy == "vector" else 16
    query = select(allowed, distance.label("distance")).where(
        distance <= 1 - settings.rag_min_similarity
    )
    vector_rows = session.execute(query.order_by(distance, allowed.c.chunk_id).limit(size)).all()
    sources = {r.chunk_id: make_citation(r, max(0, min(1, 1 - r.distance))) for r in vector_rows}
    if settings.rag_strategy == "vector":
        return list(sources.values())
    terms = lexical_terms(question)
    text = func.lower(allowed.c.title + literal(" ") + allowed.c.content)
    lexical = sum((case((func.strpos(text, term) > 0, 1), else_=0) for term in terms), literal(0))
    # Same materialized authorization CTE and semantic gate for BOTH branches.
    lexical_rows = session.execute(
        query.where(lexical > 0).order_by(lexical.desc(), allowed.c.chunk_id).limit(16)
    ).all()
    scores = {}
    for name, rows in (("vector_rank", vector_rows), ("lexical_rank", lexical_rows)):
        for rank, row in enumerate(rows, 1):
            sources.setdefault(row.chunk_id, make_citation(row, max(0, min(1, 1 - row.distance))))
            sources[row.chunk_id][name] = rank
            scores[row.chunk_id] = scores.get(row.chunk_id, 0) + 1 / (60 + rank)
    # RRF avoids adding incomparable cosine / term-count scores.
    candidates = [{**sources[key], "fusion_score": score} for key, score in scores.items()]
    candidates.sort(key=lambda p: (-p["fusion_score"], p["chunk_id"]))
    candidates = rerank(question, candidates[:16], settings)
    return candidates[: settings.rag_top_k]
