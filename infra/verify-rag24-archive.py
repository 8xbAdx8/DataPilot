"""Audit Stage24 against its ORIGINAL Git code, after later application changes."""

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "48ca7654b3bba04a35ab55ad8a54d4bfd0104546"


def verify():
    archive = subprocess.check_output(["git", "archive", COMMIT], cwd=ROOT)
    runtime = (ROOT / "data/runtime").resolve()
    with tempfile.TemporaryDirectory(
        prefix="rag24-historical-", dir=runtime
    ) as directory:
        extracted = Path(directory).resolve()
        assert extracted.is_relative_to(runtime)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            for member in source.getmembers():
                if not (
                    member.name.startswith(("backend/", "docs/rag24/"))
                    or member.name == "infra/verify-rag24-results.py"
                ):
                    continue
                target = (extracted / member.name).resolve()
                assert target.is_relative_to(extracted)
                assert member.isfile() or member.isdir()
                source.extract(member, extracted, filter="data")
        environment = {
            key: value
            for key, value in os.environ.items()
            if not any(
                word in key.upper()
                for word in ("KEY", "TOKEN", "PASSWORD", "DATABASE_URL")
            )
        }
        environment["PYTHONPATH"] = str(extracted / "backend")
        result = subprocess.run(
            [sys.executable, str(extracted / "infra/verify-rag24-results.py")],
            cwd=extracted,
            env=environment,
            capture_output=True,
            timeout=30,
            check=True,
        )
        receipt = json.loads(result.stdout)
    return {
        "historical_commit": COMMIT,
        "new_model_calls": 0,
        "source": "original Git blobs, not current changed production code",
        **receipt,
    }


if __name__ == "__main__":
    print(json.dumps(verify()))
