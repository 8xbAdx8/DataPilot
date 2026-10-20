"""Explicit upload of content-free, externally scored LangSmith experiments."""

import hashlib
import json
import math
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx

from app.errors import AppError


def require_publish_config(settings):
    if not settings.datapilot_langsmith_enabled or not settings.langsmith_api_key:
        raise AppError(
            503, "langsmith_not_configured", "上传评分需显式启用 LangSmith 并配置独立 Key。"
        )


def experiment_payloads(report, cases, project):
    version = hashlib.sha256(
        json.dumps(cases, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]
    known = {case["id"] for case in cases}
    if report["mode"] not in {
        "controlled-offline-not-model-accuracy",
        "live-chat-and-embedding-with-injected-faults",
    }:
        raise ValueError("Unknown evaluation mode")
    payloads = []
    for mode in ("baseline", "enhanced"):
        records = report["records"][mode]
        if not 0 < len(records) <= len(known) or len({r["id"] for r in records}) != len(records):
            raise ValueError("Invalid evaluation case count")
        rows = []
        for record in records:
            case_id = record["id"]
            if case_id not in known:
                raise ValueError("Only fixed repository case IDs can be uploaded")
            start, end = (datetime.fromisoformat(record[key]) for key in ("started_at", "ended_at"))
            if start.tzinfo is None or end.tzinfo is None or end < start:
                raise ValueError("Actual timezone-aware evaluation timestamps required")
            start, end = start.astimezone(UTC), end.astimezone(UTC)
            scores = []
            for name in ("correct", "result_correct", "safety_ok", "recall_at_k", "false_refusal"):
                value = record.get(name)
                if value is None:
                    continue
                if (
                    not isinstance(value, (bool, int, float))
                    or not math.isfinite(value)
                    or not 0 <= value <= 1
                ):
                    raise ValueError("Invalid bounded evaluation score")
                scores.append({"key": name, "score": float(value)})
            counts = {}
            for key in ("sql_calls", "agent_calls", "tool_calls", "repair_attempts", "tokens"):
                value = record.get(key)
                if type(value) is int and 0 <= value <= 10**9:
                    counts[key] = value
            rows.append(
                {
                    "row_id": str(
                        uuid5(NAMESPACE_URL, "datapilot-eval/" + version + "/" + case_id)
                    ),
                    "inputs": {"case_id": case_id},
                    "expected_outputs": {},
                    "actual_outputs": {},
                    "evaluation_scores": scores,
                    "start_time": start.isoformat(),
                    "end_time": end.isoformat(),
                    "run_name": "datapilot-evaluation",
                    "run_metadata": counts,
                }
            )
        payloads.append(
            {
                "dataset_name": "datapilot-external-fixed-" + version,
                "experiment_name": project + "-" + mode + "-" + uuid4().hex[:8],
                "experiment_metadata": {"evaluation_mode": report["mode"], "capabilities": mode},
                "experiment_start_time": min(r["start_time"] for r in rows),
                "experiment_end_time": max(r["end_time"] for r in rows),
                "results": rows,
            }
        )
    return payloads


def publish_report(report, cases, settings, *, transport=None):
    require_publish_config(settings)
    try:
        payloads = experiment_payloads(report, cases, settings.langsmith_project)
    except (ValueError, KeyError, TypeError) as error:
        raise AppError(
            422, "evaluation_publish_invalid", "评估评分、用例或实际时间戳不符合上传约定。"
        ) from error
    endpoint = settings.langsmith_endpoint.rstrip("/")
    if not endpoint.endswith("/api/v1"):
        endpoint += "/api/v1"
    try:
        with httpx.Client(timeout=5, transport=transport, follow_redirects=False) as client:
            for payload in payloads:
                with client.stream(
                    "POST",
                    endpoint + "/datasets/upload-experiment",
                    headers={"x-api-key": settings.langsmith_api_key},
                    json=payload,
                ) as response:
                    if response.status_code not in {200, 201}:
                        raise AppError(
                            502,
                            "langsmith_publish_failed",
                            "评分上传未确认成功，本地评估结果仍保留。",
                        )
                    content = bytearray()
                    for part in response.iter_bytes():
                        content.extend(part)
                        if len(content) > 128 * 1024:
                            raise ValueError("Response exceeds size limit")
                    body = json.loads(content)
                    if not all(
                        isinstance(body.get(k), dict) and body[k].get("id")
                        for k in ("dataset", "experiment")
                    ):
                        raise ValueError("Missing experiment acknowledgement")
        return {"status": "acknowledged", "experiments": 2}
    except AppError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
        raise AppError(
            502, "langsmith_publish_failed", "评分上传未确认成功，本地评估结果仍保留。"
        ) from error
