import json
import multiprocessing as mp
import os
from pathlib import Path
from threading import BoundedSemaphore

import duckdb

from app.config import Settings
from app.errors import AppError
from app.serialization import json_value

QUERY_SLOTS = BoundedSemaphore(2)


def _query_worker(path: str, sql: str, max_rows: int, max_bytes: int, sender):
    # Minimise inherited credentials. SQL also has no approved environment/filesystem functions.
    for key in list(os.environ):
        if any(
            word in key.upper() for word in ("KEY", "TOKEN", "PASSWORD", "DATABASE_URL", "SECRET")
        ):
            os.environ.pop(key, None)
    try:
        with duckdb.connect(
            path,
            read_only=True,
            config={
                "enable_external_access": False,
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
                "allow_unsigned_extensions": False,
                "allow_community_extensions": False,
                "allow_persistent_secrets": False,
                "python_enable_replacements": False,
                "threads": 1,
                "memory_limit": "128MB",
                "max_temp_directory_size": "0B",
                "lock_configuration": True,
            },
        ) as connection:
            cursor = connection.execute(f"SELECT * FROM ({sql}) AS guarded LIMIT {max_rows + 1}")
            columns = [{"name": column[0], "type": str(column[1])} for column in cursor.description]
            if len({c["name"].casefold() for c in columns}) != len(columns):
                sender.send({"error": "duplicate_columns"})
                return
            rows = []
            size = len(json.dumps(columns, ensure_ascii=False).encode())
            truncated = False
            while batch := cursor.fetchmany(64):
                for values in batch:
                    if len(rows) == max_rows:
                        truncated = True
                        break
                    row = {c["name"]: json_value(v) for c, v in zip(columns, values, strict=True)}
                    size += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode()) + 2
                    if size > max_bytes:
                        sender.send({"error": "result_too_large"})
                        return
                    rows.append(row)
                if truncated:
                    break
            sender.send({"columns": columns, "rows": rows, "truncated": truncated})
    except Exception:
        # Never expose engine exceptions containing paths, credentials, or full uploaded values.
        sender.send({"error": "query_failed"})
    finally:
        sender.close()


def execute_query(path: Path, sql: str, settings: Settings) -> dict:
    """Caller MUST pass SQL approved by validate_sql. Process deadline includes startup."""
    if not path.is_file():
        raise AppError(410, "dataset_expired", "数据文件已不可用，请重新上传。")
    if not QUERY_SLOTS.acquire(blocking=False):
        raise AppError(503, "query_busy", "分析执行槽已满，请稍后重试。")
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_query_worker,
        args=(str(path), sql, settings.max_result_rows, settings.max_result_bytes, sender),
        daemon=True,
    )
    try:
        process.start()
        sender.close()
        if not receiver.poll(settings.query_timeout_seconds):
            raise AppError(408, "query_timeout", "查询超过执行时间限制，执行进程已终止。")
        try:
            result = receiver.recv()
        except EOFError as exc:
            raise AppError(422, "query_failed", "查询执行进程异常退出。") from exc
        if result.get("error") == "result_too_large":
            raise AppError(413, "result_too_large", "结果超过返回字节限制，请聚合或减少查询字段。")
        if result.get("error") == "duplicate_columns":
            raise AppError(422, "duplicate_columns", "查询结果列名重复，请使用不同别名。")
        if result.get("error"):
            raise AppError(422, "query_failed", "DuckDB 查询执行失败，请检查字段类型与运算。")
        return result
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        receiver.close()
        sender.close()
        QUERY_SLOTS.release()
