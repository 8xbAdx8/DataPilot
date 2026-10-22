"""Stage24-only durable Embedding budget; older live guards are unchanged."""

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from app.embeddings import CompatibleEmbeddingProvider, validate_vectors

REQUEST_LIMIT, TOKEN_LIMIT = 80, 20000
TOKENIZER_SHA = "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a"


class BudgetStop(ValueError):
    """Fixed rejection code, never a network exception body."""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def now():
    return datetime.now(UTC).isoformat()


class TokenReservation:
    def __init__(self, path):
        from tokenizers import Tokenizer

        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != TOKENIZER_SHA:
            raise BudgetStop("tokenizer_hash_mismatch")
        self.tokenizer = Tokenizer.from_file(str(path))

    def estimate(self, texts):
        estimated = sum(len(self.tokenizer.encode(t, add_special_tokens=False).ids) for t in texts)
        # The hosted tokenizer revision is not exposed: reserve a byte-BPE upper
        # envelope and per-input margin, rather than assume identical billing.
        reserve = max(estimated * 2, sum(len(t.encode()) for t in texts)) + 32 * len(texts)
        return estimated, reserve


class Ledger:
    def __init__(self, path, authorization, *, create=False):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("xb"):
                pass
        elif not self.path.is_file():
            raise BudgetStop("ledger_missing")
        self.db = sqlite3.connect(self.path, timeout=1)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), "
            "authorization TEXT NOT NULL, stopped TEXT);"
            "CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, label TEXT NOT NULL, "
            "created TEXT NOT NULL, finished TEXT, status TEXT NOT NULL, "
            "estimated INTEGER NOT NULL, "
            "reserved INTEGER NOT NULL, actual INTEGER, usage TEXT, provider_request_id TEXT, "
            "duration_ms REAL);"
            "CREATE TABLE IF NOT EXISTS inputs (hash TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
            "vector TEXT, FOREIGN KEY(request_id) REFERENCES requests(id));"
        )
        value = json.dumps(authorization, sort_keys=True, separators=(",", ":"))
        if create:
            self.db.execute("INSERT INTO metadata VALUES (1,?,NULL)", (value,))
            self.db.commit()
        row = self.db.execute("SELECT authorization FROM metadata WHERE id=1").fetchone()
        if not row or row[0] != value:
            self.db.close()
            raise BudgetStop("authorization_mismatch")

    def close(self):
        self.db.close()

    def healthy(self, database=None):
        database = database or self.db
        if database.execute("SELECT stopped FROM metadata WHERE id=1").fetchone()[0]:
            raise BudgetStop("ledger_stopped")
        if database.execute("SELECT 1 FROM requests WHERE status!='complete'").fetchone():
            raise BudgetStop("prior_request_uncertain_no_replay")

    def cache(self, texts):
        vectors = []
        # FastAPI sync handlers run in worker threads. Do not share the writer
        # connection; the cache opens a separate strictly read-only connection.
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as read:
            read.execute("BEGIN")
            self.healthy(read)
            for text in texts:
                row = read.execute(
                    "SELECT vector FROM inputs WHERE hash=?", (digest(text),)
                ).fetchone()
                if not row or row[0] is None:
                    raise BudgetStop("cache_miss_no_network_fallback")
                vectors.append(json.loads(row[0]))
        return validate_vectors(vectors, len(texts))

    def reserve(self, texts, label, estimated, reserved):
        if (
            not 1 <= len(texts) <= 10
            or len(set(texts)) != len(texts)
            or not re.fullmatch(r"[a-z0-9_.-]{1,80}", label)
            or type(estimated) is not int
            or type(reserved) is not int
            or not 0 < estimated <= reserved <= TOKEN_LIMIT
        ):
            raise BudgetStop("invalid_reservation")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.healthy()
            count, tokens = self.db.execute(
                "SELECT COUNT(*),COALESCE(SUM(actual),0) FROM requests"
            ).fetchone()
            if count + 1 > REQUEST_LIMIT or tokens + reserved > TOKEN_LIMIT:
                raise BudgetStop("budget_exceeded_before_send")
            hashes = [digest(t) for t in texts]
            if any(
                self.db.execute("SELECT 1 FROM inputs WHERE hash=?", (h,)).fetchone()
                for h in hashes
            ):
                raise BudgetStop("duplicate_input_no_replay")
            request_id = str(uuid4())
            self.db.execute(
                "INSERT INTO requests(id,label,created,status,estimated,reserved) "
                "VALUES(?,?,?,?,?,?)",
                (request_id, label, now(), "reserved", estimated, reserved),
            )
            self.db.executemany(
                "INSERT INTO inputs(hash,request_id) VALUES(?,?)", [(h, request_id) for h in hashes]
            )
            self.db.commit()  # FULL sync before the caller can send HTTP.
            return request_id
        except Exception:
            self.db.rollback()
            raise

    def finish(self, request_id, texts, vectors, usage, provider_id, duration):
        if (
            usage.source != "provider"
            or type(usage.prompt_tokens) is not int
            or usage.prompt_tokens <= 0
            or usage.total_tokens != usage.prompt_tokens
        ):
            self.fail(request_id, "usage_missing", usage.model_dump(), provider_id, duration)
            raise BudgetStop("usage_missing_stop")
        reserved = self.db.execute(
            "SELECT reserved,status FROM requests WHERE id=?", (request_id,)
        ).fetchone()
        if not reserved or reserved[1] != "reserved":
            raise BudgetStop("request_not_pending")
        if usage.prompt_tokens > reserved[0]:
            self.fail(request_id, "reservation_exceeded", usage.model_dump(), provider_id, duration)
            raise BudgetStop("reservation_exceeded_stop")
        clean = validate_vectors(vectors, len(texts))
        try:
            self.db.execute("BEGIN IMMEDIATE")
            for text, vector in zip(texts, clean, strict=True):
                changed = self.db.execute(
                    "UPDATE inputs SET vector=? WHERE hash=? AND request_id=? AND vector IS NULL",
                    (json.dumps(vector, separators=(",", ":")), digest(text), request_id),
                ).rowcount
                if changed != 1:
                    raise BudgetStop("cache_completion_conflict")
            self.db.execute(
                "UPDATE requests SET status='complete',finished=?,actual=?,usage=?,"
                "provider_request_id=?,duration_ms=? "
                "WHERE id=? AND status='reserved'",
                (
                    now(),
                    usage.prompt_tokens,
                    json.dumps(usage.model_dump()),
                    provider_id,
                    duration,
                    request_id,
                ),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def fail(self, request_id, status, usage=None, provider_id=None, duration=None):
        actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        actual = actual if type(actual) is int and actual >= 0 else None
        with self.db:
            self.db.execute("UPDATE metadata SET stopped=? WHERE id=1", (status,))
            self.db.execute(
                "UPDATE requests SET status=?,finished=?,usage=?,actual=?,provider_request_id=?,"
                "duration_ms=? "
                "WHERE id=? AND status='reserved'",
                (status, now(), json.dumps(usage), actual, provider_id, duration, request_id),
            )

    def export(self):
        self.db.row_factory = sqlite3.Row
        metadata = json.loads(
            self.db.execute("SELECT authorization FROM metadata WHERE id=1").fetchone()[0]
        )
        requests = [dict(r) for r in self.db.execute("SELECT * FROM requests ORDER BY created,id")]
        for row in requests:
            row["usage"] = json.loads(row["usage"]) if row["usage"] else None
            row["run_id"] = metadata.get("run_id")
            row["vector_dimensions"] = 1536 if row["status"] == "complete" else None
            row["usage_complete"] = row["status"] == "complete"
            row["input_hashes"] = [
                r[0]
                for r in self.db.execute(
                    "SELECT hash FROM inputs WHERE request_id=? ORDER BY hash", (row["id"],)
                )
            ]
        return {
            "authorization": metadata,
            "limits": {"requests": REQUEST_LIMIT, "input_tokens": TOKEN_LIMIT},
            "http_requests_reserved": len(requests),
            "known_input_tokens": sum(r["actual"] or 0 for r in requests),
            "usage_complete": all(r["status"] == "complete" for r in requests),
            "stopped": self.db.execute("SELECT stopped FROM metadata WHERE id=1").fetchone()[0],
            "requests": requests,
        }


class EmbeddingTransport(httpx.BaseTransport):
    """Only one Embedding HTTP call, no redirects or automatic retry."""

    def __init__(self, endpoint, inner=None):
        self.endpoint = endpoint.rstrip("/") + "/embeddings"
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.calls, self.provider_id = 0, None

    def handle_request(self, request):
        if request.method != "POST" or str(request.url) != self.endpoint or self.calls:
            raise BudgetStop("network_scope_denied")
        self.calls += 1
        response = self.inner.handle_request(request)
        value = response.headers.get("x-request-id") or response.headers.get(
            "x-dashscope-request-id"
        )
        if value and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value):
            self.provider_id = value
        return response

    def close(self):
        self.inner.close()


def embed_batch(ledger, settings, estimator, texts, label, *, test_transport=None):
    estimate, reserve = estimator.estimate(texts)
    request_id = ledger.reserve(texts, label, estimate, reserve)
    started = time.perf_counter()
    transport = EmbeddingTransport(settings.embedding_base_url, test_transport)
    provider = CompatibleEmbeddingProvider(settings, transport=transport)
    try:
        vectors = provider.embed(texts)
        ledger.finish(
            request_id,
            texts,
            vectors,
            provider.usage,
            transport.provider_id,
            round((time.perf_counter() - started) * 1000, 3),
        )
    except Exception:
        # If even this SQLite write fails, the durable reserved row rejects
        # the next process before HTTP. It must never retry uncertain work.
        ledger.fail(
            request_id,
            "request_failed",
            provider.usage.model_dump(),
            transport.provider_id,
            round((time.perf_counter() - started) * 1000, 3),
        )
        raise BudgetStop("embedding_run_stopped_no_retry") from None
