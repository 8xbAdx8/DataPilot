"""Experimental D: bounded lexical coverage of authorized real hybrid candidates.

This is a conservative retrieval heuristic, NOT a truth/entailment model or an
answer generator. No labels, case IDs or expected source IDs enter selection.
"""

import math
import re
import unicodedata

ALIASES = {
    "保冷箱": "冷藏箱",
    "外销": "出口",
    "签贴": "标签",
    "何色": "颜色",
    "断电": "停电",
    "来电": "复电",
    "破了": "破损",
    "脏筐": "污筐",
    "清洗": "冲洗",
    "清洁筐": "冲洗周转筐",
    "晚上": "夜间",
    "进园": "入园",
    "箱温": "温度",
    "温控": "温度",
    "批准人": "确认",
    "批准": "确认",
    "坏了": "故障",
    "装果": "上果",
    "秤": "天平",
    "在哪儿": "何处",
    "摆放": "放置",
    "摆": "放置",
    "过高": "高",
}
FILLER = re.compile(
    r"什么|哪些|多少|多久|何时|何处|如何|怎样|怎么办|怎么|是否|可以|能否|需要|"
    r"可不可以|请问|条件|要求|处理|必须|需|是谁|谁|几|吗|呢|的|后|前|能|可|及|且|和|与|也|立即|有"
)
UNSAFE = re.compile(
    r"忽略.{0,8}(规则|指令)|system\s*prompt|drop\s+table|执行.{0,6}(命令|sql)", re.I
)


def normalize(value):
    value = unicodedata.normalize("NFKC", value).casefold()
    for old, new in sorted(ALIASES.items(), key=lambda item: -len(item[0])):
        value = value.replace(old, new)
    if re.search(r"红色|蓝色|绿色|白色|黄色|黑色", value):
        value += "颜色"
    return value


def features(value, *, question=False):
    value = normalize(value)
    if question:
        value = FILLER.sub("", value)
    return set(re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", value))


def select_prose(question, candidates, *, min_coverage=0.86, multi_coverage=0.94):
    wanted = features(question, question=True)
    if not wanted:
        return [], {
            "status": "insufficient_evidence",
            "reason": "empty_lexical_request",
            "scores": [],
        }
    available = [
        s
        for s in candidates
        if s.get("source_type") in {"business_rule", "data_dictionary", "dataset_description"}
        and not UNSAFE.search(s.get("excerpt", ""))
    ]
    vocabulary = [features(s.get("title", "") + " " + s.get("excerpt", "")) for s in available]
    weights = {
        token: 1 + math.log((1 + len(vocabulary)) / (1 + sum(token in v for v in vocabulary)))
        for token in wanted
    }
    denominator = sum(weights.values())

    def coverage(tokens):
        return sum(weights[t] for t in wanted & tokens) / denominator

    need_time = bool(re.search(r"何时|多久|几天|几分钟", question))
    # “如何处理” contains 何处 across word boundaries; it is not a location question.
    need_place = bool(re.search(r"何处(?!理)|哪儿|哪里|摆放", question))

    def attribute_supported(source):
        body = source.get("excerpt", "")
        return (not need_time or bool(re.search(r"天|分钟|小时|每日|前|后|立即", body))) and (
            not need_place or bool(re.search(r"处|柜|区|点|室|仓", body))
        )

    scored = []
    for source, tokens in zip(available, vocabulary, strict=True):
        score = coverage(tokens)
        scored.append((source, tokens, score))
    scored.sort(
        key=lambda row: (
            -row[2],
            -(row[0].get("fusion_score") or 0),
            -(row[0].get("similarity") or 0),
            row[0]["chunk_id"],
        )
    )
    diagnostics = [
        {
            "chunk_id": s["chunk_id"],
            "lexical_char_coverage": round(c, 6),
            "cosine": s.get("similarity"),
            "vector_rank": s.get("vector_rank"),
            "lexical_rank": s.get("lexical_rank"),
            "fusion_score": s.get("fusion_score"),
            "attribute_supported": attribute_supported(s),
        }
        for s, _, c in scored
    ]
    chosen = []
    if scored and scored[0][2] >= min_coverage and attribute_supported(scored[0][0]):
        chosen = [scored[0][0]]
    elif re.search(r"及|且|和|与|、|故障|坏了", question):
        covered = set()
        remaining = scored.copy()
        while remaining and len(chosen) < 4:
            remaining.sort(key=lambda row: -(coverage(covered | row[1]) - coverage(covered)))
            source, tokens, _ = remaining.pop(0)
            contribution = (wanted & tokens) - covered
            if len(contribution) < 2:
                break
            chosen.append(source)
            covered |= tokens
            if coverage(covered) >= multi_coverage:
                break
        if len(chosen) < 2 or coverage(covered) < multi_coverage:
            chosen = []
    return chosen, {
        "policy": "experimental_prose_D",
        "status": "retrieved" if chosen else "insufficient_evidence",
        "method": (
            "IDF-weighted lexical character/identifier coverage; "
            "not probability or semantic entailment"
        ),
        "threshold": min_coverage,
        "multi_threshold": multi_coverage,
        "scores": diagnostics,
        "source_origins": [
            {
                "chunk_id": s["chunk_id"],
                "origin": "vector_candidate" if s.get("vector_rank") else "lexical_candidate",
                "vector_rank": s.get("vector_rank"),
                "lexical_rank": s.get("lexical_rank"),
            }
            for s in chosen
        ],
        "planned_source_ids": None,
        "used_source_ids": None,
    }
