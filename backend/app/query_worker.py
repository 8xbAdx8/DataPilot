"""Private stdin/stdout worker entry; never import the API or its parent CLI."""

import json
import os
import sys
from pathlib import Path

# -I ignores PYTHONPATH and the working directory. Add only our trusted backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb  # noqa: E402

from app.serialization import json_value  # noqa: E402


def query(path, sql, max_rows, max_bytes):
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
            return {"error": "duplicate_columns"}
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
                    return {"error": "result_too_large"}
                rows.append(row)
            if truncated:
                break
        return {"columns": columns, "rows": rows, "truncated": truncated}


def main():
    for key in list(os.environ):
        if any(
            word in key.upper() for word in ("KEY", "TOKEN", "PASSWORD", "DATABASE_URL", "SECRET")
        ):
            os.environ.pop(key, None)
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("Request too large")
        request = json.loads(raw)
        if (
            set(request) != {"path", "sql", "max_rows", "max_bytes"}
            or not isinstance(request["path"], str)
            or not isinstance(request["sql"], str)
            or len(request["sql"]) > 8192
            or type(request["max_rows"]) is not int
            or not 0 < request["max_rows"] <= 10000
            or type(request["max_bytes"]) is not int
            or request["max_bytes"] <= 0
        ):
            raise ValueError("Invalid private request")
        result = query(**request)
    except Exception:
        result = {"error": "query_failed"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))


if __name__ == "__main__":
    main()
