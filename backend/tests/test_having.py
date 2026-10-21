"""New deterministic fixtures, independent of every historical frozen holdout."""

import secrets
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from app.auth import COOKIE, token_hash
from app.errors import AppError
from app.execution import execute_query
from app.main import app
from app.models import AuthSession, User
from app.providers import SqlProposal, get_provider
from app.sql_guard import validate_sql

FIELDS = [
    {"name": "category", "type": "VARCHAR"},
    {"name": "amount", "type": "DOUBLE"},
    {"name": "units", "type": "BIGINT"},
    {"name": "label", "type": "VARCHAR"},
]
CSV = b"category,amount,units,label\nA,10,1,a\nA,20,2,a\nB,,0,c\nB,5,3,d\nC,,,e\nC,,,f\nD,-2,4,g\n"


@pytest.fixture
def path(tmp_path):
    path = tmp_path / "having.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute(
            "CREATE TABLE data(category VARCHAR, amount DOUBLE, units BIGINT, label VARCHAR)"
        )
        con.execute(
            "INSERT INTO data VALUES "
            "('A',10,1,'a'),('A',20,2,'a'),('B',NULL,0,'c'),('B',5,3,'d'),('C',NULL,NULL,'e'),('C',NULL,NULL,'f'),('D',-2,4,'g')"
        )
    return path


@pytest.mark.parametrize(
    "sql,columns,rows",
    [
        (
            (
                "SELECT category,COUNT(*) AS n FROM data GROUP BY category HAVING "
                "COUNT(*)>1 ORDER BY category"
            ),
            ["category", "n"],
            [{"category": "A", "n": 2}, {"category": "B", "n": 2}, {"category": "C", "n": 2}],
        ),
        (
            (
                "SELECT category,SUM(amount) AS total FROM data GROUP BY category "
                "HAVING total>10 ORDER BY category"
            ),
            ["category", "total"],
            [{"category": "A", "total": 30}],
        ),
        (
            (
                "SELECT category,AVG(amount) AS mean FROM data GROUP BY category HAVING"
                " AVG(amount)>=5 ORDER BY category"
            ),
            ["category", "mean"],
            [{"category": "A", "mean": 15}, {"category": "B", "mean": 5}],
        ),
        (
            (
                "SELECT category,MIN(label) AS first_label FROM data GROUP BY category "
                "HAVING MIN(label)='a' ORDER BY category"
            ),
            ["category", "first_label"],
            [{"category": "A", "first_label": "a"}],
        ),
        (
            (
                "SELECT category,MAX(amount) AS maximum FROM data GROUP BY category "
                "HAVING MAX(amount)<0 ORDER BY category"
            ),
            ["category", "maximum"],
            [{"category": "D", "maximum": -2}],
        ),
        (
            (
                "SELECT category,SUM(amount) AS total FROM data GROUP BY category "
                "HAVING COUNT(*)>=2 AND (SUM(amount)>=10 OR SUM(amount) IS NULL) ORDER "
                "BY category"
            ),
            ["category", "total"],
            [{"category": "A", "total": 30}, {"category": "C", "total": None}],
        ),
        (
            (
                "SELECT category,COUNT(amount) AS n FROM data GROUP BY category HAVING "
                "COUNT(amount)=0 ORDER BY category"
            ),
            ["category", "n"],
            [{"category": "C", "n": 0}],
        ),
        (
            (
                "SELECT category,SUM(amount) AS total FROM data GROUP BY category "
                "HAVING SUM(amount)>1000 ORDER BY category"
            ),
            ["category", "total"],
            [],
        ),
        (
            (
                "SELECT category,COUNT(DISTINCT label) AS n FROM data GROUP BY category"
                " HAVING n=2 ORDER BY category"
            ),
            ["category", "n"],
            [{"category": "B", "n": 2}, {"category": "C", "n": 2}],
        ),
        (
            (
                "SELECT category,SUM(amount*units) AS total FROM data GROUP BY category"
                " HAVING total>=20 ORDER BY total DESC"
            ),
            ["category", "total"],
            [{"category": "A", "total": 50}],
        ),
        (
            (
                "SELECT category,SUM(amount) AS total FROM data GROUP BY category "
                "HAVING NOT (SUM(amount) IS NULL) AND category='B' ORDER BY category"
            ),
            ["category", "total"],
            [{"category": "B", "total": 5}],
        ),
        (
            (
                "SELECT category,SUM(amount) AS total FROM data WHERE units>0 GROUP BY "
                "category HAVING SUM(amount)/COUNT(amount)>10 ORDER BY category"
            ),
            ["category", "total"],
            [{"category": "A", "total": 30}],
        ),
    ],
)
def test_real_grouped_having_values_and_output_contract(path, settings, sql, columns, rows):
    approved = validate_sql(sql, FIELDS)
    result = execute_query(path, approved, settings)
    assert [c["name"] for c in result["columns"]] == columns
    assert result["rows"] == rows
    assert not result["truncated"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT SUM(amount) FROM data HAVING SUM(amount)>0",
        "SELECT category FROM data GROUP BY category HAVING category='A'",
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING missing>0",
        "SELECT category,SUM(category) FROM data GROUP BY category HAVING SUM(category)>0",
        "SELECT category,SUM(AVG(amount)) FROM data GROUP BY category HAVING SUM(amount)>0",
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING SUM(AVG(amount))>0",
        (
            "SELECT category,COUNT(amount,units) FROM data GROUP BY category HAVING"
            " COUNT(amount,units)>0"
        ),
        "SELECT category,SUM(amount) AS amount FROM data GROUP BY category HAVING amount>0",
        "SELECT category,SUM(amount) AS n,COUNT(*) AS n FROM data GROUP BY category HAVING n>0",
        "SELECT amount,SUM(amount) AS n FROM data GROUP BY category HAVING n>0",
        "SELECT * FROM data GROUP BY category HAVING SUM(amount)>0",
        "SELECT category,SUM(amount) AS n FROM data GROUP BY category HAVING n>0 ORDER BY units",
        (
            "SELECT category,SUM(amount) FROM data GROUP BY category HAVING "
            "SUM(amount)>(SELECT AVG(amount) FROM data)"
        ),
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING EXISTS(SELECT 1)",
        (
            "SELECT category,SUM(amount) FROM data GROUP BY category HAVING "
            "read_blob('/etc/passwd') IS NOT NULL"
        ),
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING getenv('LLM_API_KEY')='x'",
        "SELECT category,SUM(amount) FROM users GROUP BY category HAVING SUM(amount)>0",
        (
            "SELECT category,SUM(amount) FROM data GROUP BY category HAVING "
            "SUM(amount)>0; DROP TABLE data"
        ),
        (
            "SELECT category,SUM(amount) FROM data GROUP BY category HAVING "
            "SUM(amount)>0 UNION SELECT category,amount FROM data"
        ),
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING ROUND(SUM(amount))>0",
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING SUM(amount) IN (1,2)",
        "SELECT category,SUM(amount) FROM data GROUP BY category HAVING "
        + " AND ".join(["SUM(amount)>0"] * 9),
    ],
)
def test_invalid_having_fails_closed(sql):
    with pytest.raises(AppError) as error:
        validate_sql(sql, FIELDS)
    assert error.value.detail["code"] == "unsafe_sql"


class Proposal:
    def __init__(self, sql):
        self.sql, self.calls = sql, 0

    def generate(self, *args):
        self.calls += 1
        return SqlProposal(sql=self.sql, explanation="Controlled HAVING contract")


def test_having_api_runs_real_guard_graph_and_duckdb(client):
    dataset = client.post("/api/datasets", files={"file": ("having.csv", CSV)}).json()["id"]
    app.dependency_overrides[get_provider] = lambda: Proposal(
        "SELECT category,SUM(amount) AS total FROM data GROUP BY category "
        "HAVING SUM(amount)>10 ORDER BY category"
    )
    result = client.post(
        f"/api/datasets/{dataset}/analyses", json={"question": "按category聚合amount"}
    )
    assert result.status_code == 201
    body = result.json()
    assert body["result"]["rows"] == [{"category": "A", "total": 30}]
    assert body["telemetry"]["workflow"]["validations"] == [{"status": "approved", "attempt": 0}]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT category,SUM(category) FROM data GROUP BY category HAVING SUM(category)>0",
        (
            "SELECT category,COUNT(*) FROM data GROUP BY category HAVING "
            "read_blob('/etc/passwd') IS NOT NULL"
        ),
    ],
)
def test_rejected_having_never_reaches_executor(client, monkeypatch, sql):
    dataset = client.post("/api/datasets", files={"file": ("having.csv", CSV)}).json()["id"]
    app.dependency_overrides[get_provider] = lambda: Proposal(sql)
    monkeypatch.setattr(
        "app.analyses.execute_query", lambda *args: pytest.fail("Rejected SQL executed")
    )
    response = client.post(
        f"/api/datasets/{dataset}/analyses", json={"question": "按category分析amount"}
    )
    assert response.status_code == 422 and response.json()["detail"]["code"] == "unsafe_sql"


def test_foreign_dataset_is_denied_before_model_or_having_execution(client, session, monkeypatch):
    dataset = client.post("/api/datasets", files={"file": ("having.csv", CSV)}).json()["id"]
    other = User(username="having-" + secrets.token_hex(6), password_hash="unused-session-fixture")
    session.add(other)
    session.flush()
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=other.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    session.commit()
    client.cookies.set(COOKIE, token)
    provider = Proposal("SELECT category,COUNT(*) AS n FROM data GROUP BY category HAVING n>0")
    app.dependency_overrides[get_provider] = lambda: provider
    monkeypatch.setattr(
        "app.analyses.execute_query", lambda *args: pytest.fail("Unauthorized SQL executed")
    )
    assert client.get(f"/api/datasets/{dataset}").status_code == 404
    assert (
        client.post(
            f"/api/datasets/{dataset}/analyses", json={"question": "统计category"}
        ).status_code
        == 404
    )
    assert provider.calls == 0
