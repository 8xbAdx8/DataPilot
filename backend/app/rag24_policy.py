"""Opt-in experiment: registered metrics plus explicit nonmetric fact contracts.

This is not an LLM judge or a general truth classifier. Production defaults and
the Stage22 registered policy are unchanged. Nonmetric facts must be in actual
authorized vector candidates, name the subject AND requested aspect, and pass
the closed content schema; a topic-only match is insufficient.
"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.metric_resolution import mentions
from app.retrieval_evidence import assess_evidence

Anchor = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    aspect: Anchor
    aliases: list[Anchor] = Field(default_factory=list, max_length=8)
    answer: str = Field(min_length=1, max_length=400)


class KnowledgeFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    subject: Anchor
    subject_aliases: list[Anchor] = Field(default_factory=list, max_length=8)
    required_fields: list[Anchor] = Field(default_factory=list, max_length=16)
    facts: list[Fact] = Field(min_length=1, max_length=8)


def select_knowledge(registry, question, candidates):
    resolution = registry.resolve(question, question=True)
    if resolution["status"] != "not_found":
        return assess_evidence(registry, question, candidates)
    names = {f["name"] for f in registry.fields}
    matches = []
    for source in candidates:
        if source.get("source_type") not in {
            "business_rule",
            "data_dictionary",
            "dataset_description",
        }:
            continue
        try:
            document = KnowledgeFacts.model_validate(json.loads(source["excerpt"]))
        except (ValueError, KeyError, ValidationError):
            continue
        if not set(document.required_fields) <= names:
            continue
        if not any(
            mentions(question, anchor) for anchor in [document.subject, *document.subject_aliases]
        ):
            continue
        for fact in document.facts:
            if any(mentions(question, anchor) for anchor in [fact.aspect, *fact.aliases]):
                matches.append(source)
    unique = {s["chunk_id"]: s for s in matches}
    # Several matching facts, even within one document, require clarification.
    selected = list(unique.values()) if len(matches) == 1 else []
    return selected, {
        "policy": "registered_with_facts",
        "status": "knowledge" if selected else "ambiguous" if matches else "not_found",
        "candidate_count": len(candidates),
        "catalog_supplement_count": 0,
        "source_origins": [
            {"chunk_id": s["chunk_id"], "origin": "vector_candidate"} for s in selected
        ],
        "matched_fact_count": len(matches),
        "planned_source_ids": None,
        "used_source_ids": None,
    }
