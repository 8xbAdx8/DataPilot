"""Read-only local history audit. Reports locations, never matched secret values.

Run with the installed backend environment, from the repository root. Pattern
findings require review; absence of findings is not proof that no secret exists.
"""

import argparse
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings


def git(*arguments):
    return subprocess.check_output(["git", *arguments], stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--details-output",
        type=Path,
        default=Path("data/runtime")
        / ("history-audit-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".json"),
    )
    args = parser.parse_args()
    config = Settings()
    known = [
        value.encode()
        for value in (
            config.llm_api_key,
            config.embedding_api_key,
            config.langsmith_api_key,
        )
        if value and len(value) >= 10
    ]
    patterns = {
        "provider_key": rb"(?<![A-Za-z0-9])(?:sk-[A-Za-z0-9._-]{20,}|"
        rb"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
        rb"lsv2_[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})",
        "private_key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        "credential_url": rb"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@\"']+:[^\s/@\"']+@[^\s\"']+",
        "secret_assignment": rb"(?im)^[ \t]*(?:LLM_API_KEY|EMBEDDING_API_KEY|"
        rb"LANGSMITH_API_KEY|OPENAI_API_KEY|DEEPSEEK_API_KEY|PASSWORD|POSTGRES_PASSWORD)"
        rb"[ \t]*[:=][ \t]*[^\s#\r\n]+",
        "local_path": rb"[A-Za-z]:[\\/](?:Users[\\/][^\s\\/]+|codexproject)[^\r\n`\"']*",
    }
    commits = git("rev-list", "--all").decode().splitlines()
    paths = {}
    forbidden = []
    for commit in commits:
        for entry in git("ls-tree", "-rz", commit).split(b"\0"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            _, kind, object_id = metadata.decode().split()
            if kind != "blob":
                continue
            path = path.decode(errors="replace")
            paths.setdefault(object_id, set()).add(path)
            if (
                re.search(
                    r"(^|/)(\.env(?:\.[^/]+)?|id_rsa|id_ed25519|credentials\.json)$",
                    path,
                )
                and not path.endswith(".env.example")
            ) or path.endswith(
                (".pem", ".key", ".log", ".duckdb", ".sqlite", ".sqlite3")
            ):
                forbidden.append({"commit": commit, "path": path})
    findings = []
    exact = []
    objects = {
        line.split()[0] for line in git("rev-list", "--objects", "--all").splitlines()
    }
    process = subprocess.Popen(
        ["git", "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    try:
        for object_id in sorted(objects):
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            content = process.stdout.read(int(header[2]))
            process.stdout.read(1)
            if header[1] not in (b"blob", b"commit", b"tag"):
                continue
            location = {
                "object": object_id.decode(),
                "type": header[1].decode(),
                "paths": sorted(paths.get(object_id.decode(), [])),
            }
            if any(value in content for value in known):
                exact.append(location)
            if b"\0" in content:
                continue
            for rule, pattern in patterns.items():
                matches = list(re.finditer(pattern, content))
                if matches:
                    findings.append(
                        {
                            **location,
                            "rule": rule,
                            "lines": [
                                content[: m.start()].count(b"\n") + 1 for m in matches
                            ],
                        }
                    )
    finally:
        process.stdin.close()
        process.wait()
    result = {
        "head": git("rev-parse", "HEAD").decode().strip(),
        "commits": len(commits),
        "blobs": len(paths),
        "configured_secret_count": len(known),
        "exact_secret_matches": exact,
        "forbidden_paths": forbidden,
        "pattern_findings": findings,
        "note": "Candidates need manual review; no matched values are stored.",
    }
    output = args.details_output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "commits": len(commits),
                "blobs": len(paths),
                "exact_secret_matches": len(exact),
                "forbidden_path_occurrences": len(forbidden),
                "candidate_groups": dict(Counter(item["rule"] for item in findings)),
                "details": str(output),
            }
        )
    )
    return 1 if exact or forbidden else 0


if __name__ == "__main__":
    raise SystemExit(main())
