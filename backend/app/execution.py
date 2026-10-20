import json
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import BoundedSemaphore

from app.config import Settings
from app.errors import AppError

QUERY_SLOTS = BoundedSemaphore(2)


def execute_query(path: Path, sql: str, settings: Settings) -> dict:
    """Caller MUST pass approved SQL. Deadline includes isolated interpreter startup."""
    if not path.is_file():
        raise AppError(410, "dataset_expired", "数据文件已不可用，请重新上传。")
    if not QUERY_SLOTS.acquire(blocking=False):
        raise AppError(503, "query_busy", "分析执行槽已满，请稍后重试。")
    process = None
    deadline = time.monotonic() + settings.query_timeout_seconds
    try:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(
                word in key.upper()
                for word in ("KEY", "TOKEN", "PASSWORD", "DATABASE_URL", "SECRET")
            )
        }
        request = json.dumps(
            {
                "path": str(path.resolve()),
                "sql": sql,
                "max_rows": settings.max_result_rows,
                "max_bytes": settings.max_result_bytes,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        # No shell, argv SQL or multiprocessing main replay. -I ignores Python environment paths.
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).with_name("query_worker.py"))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            raw, _ = process.communicate(request, timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise AppError(408, "query_timeout", "查询超过执行时间限制，执行进程已终止。") from exc
        if process.returncode or len(raw) > settings.max_result_bytes + 4096:
            raise AppError(422, "query_failed", "查询执行进程异常退出。")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise AppError(422, "query_failed", "查询执行进程异常退出。") from exc
        if result.get("error") == "result_too_large":
            raise AppError(413, "result_too_large", "结果超过返回字节限制，请聚合或减少查询字段。")
        if result.get("error") == "duplicate_columns":
            raise AppError(422, "duplicate_columns", "查询结果列名重复，请使用不同别名。")
        if result.get("error"):
            raise AppError(422, "query_failed", "DuckDB 查询执行失败，请检查字段类型与运算。")
        return result
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()
        QUERY_SLOTS.release()
