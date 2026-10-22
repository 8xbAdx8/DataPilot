"""Durable opt-in trial ledger. Ordinary app/old live entry points never use it."""

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from uuid import uuid4

import httpx

from app.embeddings import validate_vectors
from app.rag24_budget import BudgetStop, TokenReservation

LIMITS = {"chat": (30, 20000), "embedding": (50, 15000)}
STAGE_LIMITS = {
    (25, "chat"): (12, 8000),
    (29, "chat"): (18, 12000),
    (25, "embedding"): (8, 2500),
    (26, "embedding"): (8, 2000),
    (27, "embedding"): (20, 6500),
    (29, "embedding"): (14, 4000),
}


def stamp():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def synchronized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)

    return locked


class InputEstimator:
    """Pinned local tokenizer + explicit reserve; provider usage remains authoritative."""

    def __init__(self, path):
        self.local = TokenReservation(path)

    def estimate(self, kind, payload):
        if kind == "embedding":
            texts = payload.get("input")
            if not isinstance(texts, list) or not 1 <= len(texts) <= 10:
                raise BudgetStop("invalid_embedding_batch")
            if not all(isinstance(t, str) and t for t in texts):
                raise BudgetStop("invalid_embedding_input")
            return self.local.estimate(texts)
        # Count the complete serialized message/tool schemas, not just question.
        # Qwen is not the hosted chat tokenizer: 50% + 128 framing reserve.
        # If provider usage exceeds this reserve, stop globally without retry.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        estimate = len(self.local.tokenizer.encode(encoded, add_special_tokens=False).ids)
        return estimate, math.ceil(estimate * 1.5) + 128


class TrialLedger:
    def __init__(self, path, authorization, *, create=False):
        self.path = Path(path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("xb"):
                pass
        elif not self.path.is_file():
            raise BudgetStop("trial_ledger_missing")
        self.lock = RLock()
        self.db = sqlite3.connect(self.path, timeout=3, check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS metadata(id INTEGER PRIMARY KEY CHECK(id=1),"
            "authorization TEXT NOT NULL,stopped TEXT);"
            "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,stage INTEGER,kind TEXT,"
            "case_id TEXT,channel TEXT,provider TEXT,model TEXT,created TEXT,finished TEXT,"
            "status TEXT,estimated INTEGER,reserved INTEGER,input_tokens INTEGER,"
            "output_tokens INTEGER,total_tokens INTEGER,provider_id TEXT,duration_ms REAL,"
            "payload_hash TEXT);"
            "CREATE TABLE IF NOT EXISTS vectors(space TEXT,hash TEXT,request_id TEXT,vector TEXT,"
            "PRIMARY KEY(space,hash));"
        )
        canonical = json.dumps(authorization, sort_keys=True, separators=(",", ":"))
        if create:
            self.db.execute("INSERT INTO metadata VALUES(1,?,NULL)", (canonical,))
            self.db.commit()
        row = self.db.execute("SELECT authorization FROM metadata WHERE id=1").fetchone()
        if not row or row[0] != canonical:
            self.db.close()
            raise BudgetStop("trial_authorization_mismatch")

    def close(self):
        self.db.close()

    @synchronized
    def healthy(self, connection=None):
        connection = connection or self.db
        if connection.execute("SELECT stopped FROM metadata WHERE id=1").fetchone()[0]:
            raise BudgetStop("trial_globally_stopped")
        if connection.execute("SELECT 1 FROM requests WHERE status!='complete'").fetchone():
            raise BudgetStop("trial_uncertain_request_no_replay")

    @synchronized
    def reserve(self, stage, kind, case, channel, provider, model, payload, estimate, reserve):
        if (stage, kind) not in STAGE_LIMITS:
            raise BudgetStop("trial_stage_kind_not_authorized")
        if type(estimate) is not int or type(reserve) is not int or not 0 < estimate <= reserve:
            raise BudgetStop("trial_invalid_reservation")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", case + "." + channel):
            raise BudgetStop("trial_invalid_case")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.healthy()
            for scope, limits in ((None, LIMITS[kind]), (stage, STAGE_LIMITS[stage, kind])):
                query = "SELECT COUNT(*),COALESCE(SUM(input_tokens),0) FROM requests WHERE kind=?"
                args = [kind]
                if scope is not None:
                    query += " AND stage=?"
                    args.append(scope)
                count, tokens = self.db.execute(query, args).fetchone()
                if count + 1 > limits[0] or tokens + reserve > limits[1]:
                    raise BudgetStop("trial_budget_preflight_denied")
            request_id = str(uuid4())
            self.db.execute(
                "INSERT INTO requests(id,stage,kind,case_id,channel,provider,model,created,"
                "status,estimated,reserved,payload_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    stage,
                    kind,
                    case,
                    channel,
                    provider,
                    model,
                    stamp(),
                    "reserved",
                    estimate,
                    reserve,
                    digest(json.dumps(payload, ensure_ascii=False, sort_keys=True)),
                ),
            )
            self.db.commit()  # Durable reservation MUST precede the HTTP send.
            return request_id
        except Exception:
            self.db.rollback()
            raise

    @synchronized
    def stop(self, request_id, reason, usage=None):
        usage = usage if isinstance(usage, dict) else {}
        known = [
            value if type(value) is int and value >= 0 else None
            for value in (
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("total_tokens"),
            )
        ]
        with self.db:
            self.db.execute("UPDATE metadata SET stopped=? WHERE id=1", (reason,))
            self.db.execute(
                "UPDATE requests SET status=?,finished=?,input_tokens=COALESCE(input_tokens,?),"
                "output_tokens=COALESCE(output_tokens,?),total_tokens=COALESCE(total_tokens,?) "
                "WHERE id=? AND status='reserved'",
                (reason, stamp(), *known, request_id),
            )

    @synchronized
    def complete(self, request_id, body, provider_id, duration, vector_cache=None):
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        row = self.db.execute(
            "SELECT kind,reserved,status FROM requests WHERE id=?", (request_id,)
        ).fetchone()
        if not row or row[2] != "reserved":
            raise BudgetStop("trial_request_not_reserved")
        prompt, output, total = (
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )
        valid = type(prompt) is int and prompt > 0 and type(total) is int
        if row[0] == "chat":
            valid = valid and type(output) is int and output >= 0 and prompt + output == total
        else:
            valid = valid and prompt == total and output in (None, 0)
        if not valid or not provider_id:
            self.stop(request_id, "usage_or_provider_id_missing")
            raise BudgetStop("trial_usage_missing_stop")
        # Even an exceeded reservation is a known billable response: preserve its
        # actual usage before stopping. Never turn an unknown attempt into zero.
        status = "complete" if prompt <= row[1] else "reservation_exceeded"
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "UPDATE requests SET status=?,finished=?,input_tokens=?,output_tokens=?,"
                "total_tokens=?,provider_id=?,duration_ms=? WHERE id=? AND status='reserved'",
                (status, stamp(), prompt, output, total, provider_id, duration, request_id),
            )
            if status != "complete":
                self.db.execute("UPDATE metadata SET stopped=? WHERE id=1", (status,))
            elif vector_cache:
                space, inputs, vectors = vector_cache
                for text, vector in zip(
                    inputs, validate_vectors(vectors, len(inputs)), strict=True
                ):
                    self.db.execute(
                        "INSERT INTO vectors VALUES(?,?,?,?)",
                        (space, digest(text), request_id, json.dumps(vector)),
                    )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        if status != "complete":
            raise BudgetStop("trial_reservation_exceeded_stop")

    def cache(self, space, texts):
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)) as read:
            read.execute("BEGIN")
            self.healthy(read)
            vectors = []
            for text in texts:
                row = read.execute(
                    "SELECT vector FROM vectors WHERE space=? AND hash=?", (space, digest(text))
                ).fetchone()
                if not row:
                    raise BudgetStop("trial_cache_miss_no_network_fallback")
                vectors.append(json.loads(row[0]))
        return validate_vectors(vectors, len(texts))

    @synchronized
    def export(self):
        names = [r[1] for r in self.db.execute("PRAGMA table_info(requests)")]
        rows = [
            dict(zip(names, r, strict=True))
            for r in self.db.execute("SELECT * FROM requests ORDER BY created,id")
        ]
        return {
            "requests": rows,
            "stopped": self.db.execute("SELECT stopped FROM metadata").fetchone()[0],
            "usage_complete": all(r["status"] == "complete" for r in rows),
            "totals": {
                kind: {
                    "requests": sum(r["kind"] == kind for r in rows),
                    "known_input_tokens": sum(
                        r["input_tokens"] or 0 for r in rows if r["kind"] == kind
                    ),
                    "unknown_requests": sum(
                        r["input_tokens"] is None for r in rows if r["kind"] == kind
                    ),
                }
                for kind in LIMITS
            },
            "limits": LIMITS,
            "cost": "unknown_no_invoice",
        }


class MeteredTransport(httpx.BaseTransport):
    """No retries, redirects, streaming, non-model routes or credential logging."""

    def __init__(
        self,
        ledger,
        estimator,
        *,
        stage,
        kind,
        case,
        channel,
        provider,
        model,
        base_url,
        space=None,
        inner=None,
        observe=None,
    ):
        self.ledger, self.estimator = ledger, estimator
        self.stage, self.kind, self.case, self.channel = stage, kind, case, channel
        self.provider, self.model, self.space = provider, model, space
        route = "/embeddings" if kind == "embedding" else "/chat/completions"
        self.endpoint = base_url.rstrip("/") + route
        self.inner = inner or httpx.HTTPTransport(retries=0)
        self.observe = observe

    def handle_request(self, request):
        if request.method != "POST" or str(request.url) != self.endpoint:
            raise BudgetStop("trial_network_scope_denied")
        payload = json.loads(request.content)
        if payload.get("model") != self.model or payload.get("stream"):
            raise BudgetStop("trial_model_or_stream_denied")
        if self.kind == "embedding":
            inputs = payload.get("input", [])
            if len(set(inputs)) != len(inputs):
                raise BudgetStop("trial_duplicate_embedding_input")
            for value in inputs:
                try:
                    self.ledger.cache(self.space, [value])
                except BudgetStop as error:
                    if str(error) != "trial_cache_miss_no_network_fallback":
                        raise
                else:
                    raise BudgetStop("trial_already_cached_use_readonly_cache")
        estimate, reserve = self.estimator.estimate(self.kind, payload)
        request_id = self.ledger.reserve(
            self.stage,
            self.kind,
            self.case,
            self.channel,
            self.provider,
            self.model,
            payload,
            estimate,
            reserve,
        )
        started = time.perf_counter()
        body = {}
        try:
            response = self.inner.handle_request(request)
            raw = response.read()
            if len(raw) > 512 * 1024:
                raise BudgetStop("trial_response_too_large")
            body = json.loads(raw)
            provider_id = (
                response.headers.get("x-request-id")
                or response.headers.get("x-dashscope-request-id")
                or body.get("id")
            )
            if not isinstance(provider_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,160}", provider_id
            ):
                provider_id = None
            cached = None
            if self.kind == "embedding" and response.status_code == 200:
                if body.get("model") != self.model:
                    raise BudgetStop("trial_embedding_model_mismatch")
                rows = sorted(body["data"], key=lambda row: row["index"])
                if [r["index"] for r in rows] != list(range(len(payload["input"]))):
                    raise BudgetStop("trial_vector_indices_invalid")
                cached = (self.space, payload["input"], [r["embedding"] for r in rows])
            self.ledger.complete(
                request_id,
                body,
                provider_id,
                round((time.perf_counter() - started) * 1000, 3),
                cached,
            )
            if self.observe:
                self.observe(self.case, self.channel, payload, body)
            # response.read() has already decoded any HTTP content encoding.
            # Do not make httpx decode the buffered body a second time.
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-encoding", "content-length", "transfer-encoding"}
            }
            return httpx.Response(
                response.status_code, headers=headers, content=raw, request=request
            )
        except Exception:
            # A failed write still leaves a durable reserved row; next request
            # is denied even if recording this stop itself fails.
            self.ledger.stop(
                request_id,
                "request_failed_no_retry",
                body.get("usage") if isinstance(body, dict) else None,
            )
            raise BudgetStop("trial_request_failed_stop") from None

    def close(self):
        self.inner.close()
