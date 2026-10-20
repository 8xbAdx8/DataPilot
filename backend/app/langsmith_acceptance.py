"""Explicit synthetic metadata-only cloud acceptance and evaluation publication."""

import argparse
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.config import Settings
from app.rag_evaluate import digest, evaluate, load_corpus
from app.tracing import client_for, safe_metadata, trace_payload


def publication_preview(report):
    corpus, hashes = load_corpus()
    if report["corpus"]["sha256"] != hashes["sha256"]:
        raise ValueError("Reviewed corpus mismatch")
    known = {c["id"]: c for c in corpus["queries"]}
    examples = []
    for record in report["records"]:
        case = known[record["id"]]
        # No questions, SQL, source IDs, titles, knowledge, prompts, raw rows or vectors.
        examples.append(
            {
                "inputs": {"sample_id": case["id"]},
                "outputs": {
                    "expected_sources": len(case["relevant_chunk_ids"]),
                    "should_no_match": case["should_no_match"],
                },
                "metadata": {"split": case["split"]},
            }
        )
    config = report["configuration"]
    public_config = {
        key: config[key]
        for key in ("embedding_dimensions", "rag_top_k", "rag_min_similarity", "reranker_min_score")
    }
    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in public_config.values()
    ):
        raise ValueError("Numeric configuration fields required")
    for key, allowed in {
        "rag_strategy": {"vector", "hybrid"},
        "rag_reranker": {"none", "flashrank"},
        "embedding_provider": {"openai", "openai_compatible", "fake"},
        "llm_provider": {"openai", "openai_compatible", "deepseek", "mock"},
        "llm_model": {"deepseek-flash", "deepseek-chat", "gpt-4o-mini"},
        "embedding_model": {"text-embedding-v4", "text-embedding-3-small"},
    }.items():
        public_config[key] = config.get(key) if config.get(key) in allowed else "custom"
    source_hashes = {
        name: value
        for name, value in report["source_sha256"].items()
        if name
        in {
            "knowledge.py",
            "embeddings.py",
            "analysis_agent.py",
            "analysis_graph.py",
            "providers.py",
            "retrieval.py",
            "reranking.py",
            "config.py",
            "rag_evaluate.py",
        }
        and isinstance(value, str)
        and re.fullmatch(r"[a-f0-9]{64}", value)
    }
    if not re.fullmatch(r"[a-f0-9]{40}", report["head"]):
        raise ValueError("Git parent SHA required")
    return {
        "schema": "datapilot-rag-metadata-v1",
        "corpus_sha256": hashes["sha256"],
        "report_sha256": digest(report),
        "code_sha": report["head"],
        "working_tree_dirty": bool(report.get("working_tree_dirty")),
        "code_source_sha256": source_hashes,
        "configuration": public_config,
        "examples": examples,
        "scores": {
            k: v for k, v in report["summary"].items() if type(v) in (int, float, bool) or v is None
        },
        "prompt_version": report["source_sha256"]["analysis_agent.py"],
    }


def assert_private_trace(run, expected_names):
    all_runs, pending = [], [(run, 0)]
    while pending:
        saved, depth = pending.pop()
        all_runs.append((saved, depth))
        if len(all_runs) > 32:
            raise ValueError("Unexpected trace tree size")
        pending.extend((child, depth + 1) for child in saved.child_runs or [])
    actual_names = {r.name for r, _ in all_runs}
    if not expected_names <= actual_names:
        raise ValueError("Cloud trace missing expected nodes/tools")
    for saved, depth in all_runs:
        if saved.inputs or saved.outputs or saved.serialized:
            raise ValueError("Cloud trace unexpectedly contains model inputs/outputs")
        extra = saved.extra or {}
        if set(extra) - {"metadata"}:
            raise ValueError("Unexpected SDK runtime/environment payload")
        metadata = dict(extra.get("metadata", {}))
        # LangSmith adds this system field on storage, after the SDK's outgoing
        # filter. Accept only an integer equal to the actual tree level.
        if "ls_run_depth" in metadata:
            system_depth = metadata.pop("ls_run_depth")
            if type(system_depth) is not int or system_depth != depth:
                raise ValueError("Unexpected server trace depth")
        if metadata != safe_metadata(metadata):
            raise ValueError("Cloud metadata outside the privacy allowlist")
        if saved.end_time is None or saved.start_time is None:
            raise ValueError("Cloud stage timing missing")
    return sorted(actual_names)


def rejection_trace(settings):
    """Real cloud transport of an explicitly controlled local SQL safety failure."""
    from app.analysis_graph import AnalysisRun, execute_analysis_graph
    from app.providers import MockProvider, SqlProposal

    class RejectedProposal(MockProvider):
        def generate(self, *args):
            return SqlProposal(sql="DROP TABLE data", explanation="Controlled safety regression")

        def generate_with_context(self, *args):
            return self.generate(*args)

    executed = []
    local = settings.model_copy(
        update={"llm_provider": "mock", "agent_enabled": False, "embedding_provider": "none"}
    )
    run = AnalysisRun(
        RejectedProposal(),
        local,
        {},
        lambda sql: executed.append(True),
        "controlled synthetic safety failure",
        [{"name": "value", "type": "INTEGER"}],
    )
    started = time.perf_counter()
    from app.errors import AppError

    try:
        execute_analysis_graph(run)
    except AppError as error:
        if error.detail["code"] != "unsafe_sql":
            raise
    if executed or "execute" in run.nodes or not run.failure:
        raise ValueError("Safety rejection must prove zero execution")
    result = confirm_trace(
        settings, trace_payload(run, "rejected", int((time.perf_counter() - started) * 1000))
    )
    result["mode"] = "controlled-SQL-failure-real-cloud-not-real-model"
    result["zero_execution"] = True
    return result


def confirm_trace(settings, rows):
    if not settings.datapilot_langsmith_enabled or not settings.langsmith_api_key:
        raise ValueError("Cloud acceptance requires explicit opt-in and local credentials")
    errors = []
    client = client_for(settings, lambda _: errors.append(True), timeout_ms=(5000, 10000))
    try:
        for row in rows:
            row["session_name"] = settings.langsmith_project
        client.batch_ingest_runs(create=rows)
        if errors:
            raise ValueError("Cloud transport callback reported failure")
        # A locally generated UUID or batch acknowledgement alone is NOT evidence.
        # Read the actual server record and use its returned ID and SDK URL.
        expected = {r["name"] for r in rows}
        for attempt in range(6):
            try:
                saved = client.read_run(rows[0]["id"], load_child_runs=True)
                names = assert_private_trace(saved, expected)
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(1)
        if str(saved.id) != str(rows[0]["id"]):
            raise ValueError("Server run identity mismatch")
        return {
            "status": "server-readback-confirmed",
            "run_id": str(saved.id),
            "url": client.get_run_url(run=saved),
            "names": names,
            "project": settings.langsmith_project,
            "inputs_outputs_empty": True,
            "metadata": saved.extra.get("metadata", {}),
        }
    finally:
        client.close(timeout=0)


def publish_experiment(settings, preview, report):
    errors = []
    client = client_for(settings, lambda _: errors.append(True), timeout_ms=(5000, 10000))
    # Unique additive experiment; never replace an older score/dataset.
    name = "datapilot-rag-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    try:
        dataset = client.create_dataset(name, description="Synthetic ID/count labels only")
        examples = client.create_examples(dataset_id=dataset.id, examples=preview["examples"])
        saved_examples = list(client.list_examples(dataset_id=dataset.id, limit=50))
        if len(saved_examples) != len(preview["examples"]):
            raise ValueError("Cloud dataset example count mismatch")
        by_id = {x.inputs["sample_id"]: x for x in saved_examples}
        project = client.create_project(
            name + "-experiment",
            reference_dataset_id=dataset.id,
            metadata={
                k: preview[k]
                for k in (
                    "corpus_sha256",
                    "report_sha256",
                    "code_sha",
                    "working_tree_dirty",
                    "code_source_sha256",
                    "configuration",
                    "prompt_version",
                )
            },
        )
        runs = []
        for record in report["records"]:
            run_id, now = uuid4(), datetime.now(UTC)
            metadata = {"status": "success", "retrieved_count": len(record["retrieved_ids"])}
            client.create_run(
                "datapilot.rag-evaluation",
                inputs={},
                outputs={},
                run_type="chain",
                id=run_id,
                project_name=project.name,
                start_time=now,
                end_time=now,
                reference_example_id=by_id[record["id"]].id,
                extra={"metadata": metadata},
            )
            runs.append(run_id)
        # Aggregate deterministic scores attach to the experiment, not a judge rewrite.
        feedback = []
        for key, value in preview["scores"].items():
            if type(value) in (int, float) and value is not None:
                saved = client.create_feedback(None, key, score=value, project_id=project.id)
                feedback.append(str(saved.id))
        for _attempt in range(6):
            found = list(client.list_runs(project_id=project.id, limit=50))
            if {str(r.id) for r in found} == {str(r) for r in runs}:
                break
            time.sleep(1)
        if errors or {str(r.id) for r in found} != {str(r) for r in runs}:
            raise ValueError("Cloud experiment run readback not confirmed")
        for example in saved_examples:
            if set(example.inputs) != {"sample_id"} or set(example.outputs or {}) != {
                "expected_sources",
                "should_no_match",
            }:
                raise ValueError("Cloud example fields outside preview")
        return {
            "status": "server-readback-confirmed",
            "dataset_id": str(dataset.id),
            "experiment_id": str(project.id),
            "experiment_name": project.name,
            "examples_confirmed": len(saved_examples),
            "runs_confirmed": len(found),
            "feedback_ids": feedback,
            "create_examples_acknowledged": examples is not None,
        }
    finally:
        client.close(timeout=0)


def verify_existing_trace(settings, run_id, browser):
    """Read back an actual service trace; never reconstruct spans from UI timings."""
    if browser.get("status") != "passed" or browser.get("provider") == "mock":
        raise ValueError("Successful real browser evidence required")
    workflow = browser["telemetry"]["workflow"]
    if not workflow["rag"]["used"] or workflow["agent"]["mode"] != "native_tools":
        raise ValueError("Actual RAG and native tools required")
    expected = {
        "datapilot.analysis",
        "agent_model",
        *workflow["nodes"],
        *workflow["agent"]["tools_used"],
    }
    client = client_for(settings, lambda _: None, timeout_ms=(5000, 10000))
    try:
        saved = client.read_run(run_id, load_child_runs=True)
        names = assert_private_trace(saved, expected)
        return {
            "status": "server-readback-confirmed",
            "run_id": str(saved.id),
            "url": client.get_run_url(run=saved),
            "names": names,
            "project": settings.langsmith_project,
            "inputs_outputs_empty": True,
            "metadata": saved.extra.get("metadata", {}),
        }
    finally:
        client.close(timeout=0)


def feedback_matches(actual, expected):
    # Current cloud feedback storage quantizes numeric scores to four decimals.
    # Accept exact precision or that explicit representation, never change local scores.
    return (
        type(actual) in (int, float)
        and math.isfinite(actual)
        and (abs(actual - expected) <= 1e-8 or abs(actual - round(expected, 4)) <= 1e-8)
    )


def verify_experiment(settings, preview, receipt):
    client = client_for(settings, lambda _: None, timeout_ms=(5000, 10000))
    try:
        project = client.read_project(project_id=receipt["experiment_id"])
        metadata = (project.extra or {}).get("metadata", {})
        keys = (
            "corpus_sha256",
            "report_sha256",
            "code_sha",
            "working_tree_dirty",
            "code_source_sha256",
            "configuration",
            "prompt_version",
        )
        if any(metadata.get(key) != preview[key] for key in keys):
            raise ValueError("Cloud experiment provenance mismatch")
        expected = {x["inputs"]["sample_id"]: x for x in preview["examples"]}
        examples = list(client.list_examples(dataset_id=receipt["dataset_id"], limit=50))
        if len(examples) != len(expected) or any(
            x.inputs != expected[x.inputs["sample_id"]]["inputs"]
            or x.outputs != expected[x.inputs["sample_id"]]["outputs"]
            for x in examples
        ):
            raise ValueError("Cloud labels differ from preview")
        scores = [
            (key, value) for key, value in preview["scores"].items() if type(value) in (int, float)
        ]
        if len(scores) != len(receipt["feedback_ids"]):
            raise ValueError("Cloud feedback count mismatch")
        for (key, value), feedback_id in zip(scores, receipt["feedback_ids"], strict=True):
            actual = client.read_feedback(feedback_id)
            if actual.key != key or not feedback_matches(actual.score, value):
                raise ValueError("Cloud numeric feedback differs from local authority")
        return {
            **receipt,
            "labels_readback_verified": True,
            "provenance_readback_verified": True,
            "feedback_readback_verified": len(scores),
            "feedback_representation": "cloud four-decimal quantization; "
            "local full precision authoritative",
            "url": project.url,
        }
    finally:
        client.close(timeout=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--publish-experiment", action="store_true")
    parser.add_argument("--verify-run", help="Read an existing actual service run UUID")
    parser.add_argument("--browser-evidence", type=Path)
    parser.add_argument("--verify-rejection", action="store_true")
    parser.add_argument("--verify-experiment-receipt", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_bytes())
    preview = publication_preview(report)
    if not args.live:
        with args.preview.open("x", encoding="utf-8") as stream:
            json.dump(preview, stream, indent=2)
        print(
            json.dumps(
                {
                    "status": "preview-only-no-network",
                    "examples": len(preview["examples"]),
                    "fields": ["sample_id", "counts", "labels", "metrics", "config/code hashes"],
                }
            )
        )
        return
    if os.environ.get("RUN_LIVE_LANGSMITH") != "1" or not args.output:
        parser.error("Set RUN_LIVE_LANGSMITH=1, --live and a new --output")
    if json.loads(args.preview.read_bytes()) != preview:
        parser.error("Preview differs from current report; review a new preview first")
    settings = Settings()
    if not settings.langsmith_api_key:
        parser.error("Configure the private local LangSmith key first")
    settings = settings.model_copy(update={"datapilot_langsmith_enabled": True})
    captured = []

    def capture(local_settings, run, status, duration):
        captured.append(trace_payload(run, status, duration))
        return "disabled"  # explicit synchronous send only after real synthetic analysis

    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "trace": {"status": "not_confirmed"},
        "experiment": {"status": "not_requested"},
    }
    args.output.touch(exist_ok=False)
    try:
        if args.verify_run:
            if not args.browser_evidence:
                raise ValueError("Browser evidence is required for service trace verification")
            result["failure_stage"] = "trace_readback"
            result["trace"] = verify_existing_trace(
                settings, args.verify_run, json.loads(args.browser_evidence.read_bytes())
            )
        else:
            with patch("app.analyses.export_trace", capture):
                real = evaluate(
                    os.environ["EVAL_DATABASE_URL"],
                    "development",
                    args.output.with_suffix(".analysis.json"),
                    run_downstream=True,
                    updates={
                        k: report["configuration"][k]
                        for k in (
                            "rag_strategy",
                            "rag_reranker",
                            "rag_min_similarity",
                            "reranker_min_score",
                        )
                    },
                    case_ids={"d-p-02"},
                )
            evidence = real["records"][0]["downstream"]
            if (
                evidence["status"] != "success"
                or not evidence["agent"].get("tool_calls")
                or len(captured) != 1
            ):
                raise ValueError("Actual Agent/SQL workflow did not complete")
            result["trace"]["local_pending_id"] = str(captured[0][0]["id"])
            result["failure_stage"] = "trace"
            result["trace"] = confirm_trace(settings, captured[0])
        if args.publish_experiment:
            result["failure_stage"] = "experiment"
            result["experiment"] = publish_experiment(settings, preview, report)
        if args.verify_rejection:
            result["failure_stage"] = "controlled_rejection_trace"
            result["rejection_trace"] = rejection_trace(settings)
        if args.verify_experiment_receipt:
            result["failure_stage"] = "experiment_readback"
            receipt = json.loads(args.verify_experiment_receipt.read_bytes())
            result["experiment"] = verify_experiment(settings, preview, receipt["experiment"])
    except Exception as error:
        result["failure_type"] = type(error).__name__
        result["status"] = "not_fully_confirmed"
    else:
        result["status"] = "confirmed"
        result.pop("failure_stage", None)
    finally:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    if result["status"] != "confirmed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
