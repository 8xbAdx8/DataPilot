import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import Analysis, Dataset


@pytest.fixture
def histories(session, auth_user):
    datasets = [
        Dataset(
            id=str(uuid.uuid4()),
            owner_id=auth_user.id,
            name=f"history-{i}.csv",
            file_type="csv",
            row_count=1,
            column_count=1,
            profile={},
        )
        for i in range(2)
    ]
    session.add_all(datasets)
    session.flush()
    created = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        Analysis(
            id=str(uuid.UUID(int=i + 1)),
            dataset_id=datasets[0 if i < 24 else 1].id,
            question=f"问题 {i} Revenue 100% under_score / literal",
            sql="SELECT SUM(revenue) FROM data" if i % 3 else None,
            provider="mock",
            model="history-test",
            status=["success", "error", "rejected"][i % 3],
            created_at=created + timedelta(seconds=i // 2),
        )
        for i in range(26)
    ]
    session.add_all(rows)
    session.commit()
    return datasets, rows


def test_history_pagination_has_stable_order_and_no_duplicate_ties(client, histories):
    datasets, rows = histories
    params = {"dataset_id": datasets[0].id}
    pages = [
        client.get("/api/analyses", params={**params, "limit": 7, "offset": i}).json()
        for i in range(0, 28, 7)
    ]
    assert [row["id"] for page in pages for row in page] == [row.id for row in reversed(rows[:24])]
    assert len(client.get("/api/analyses", params=params).json()) == 20
    assert client.get("/api/analyses", params={**params, "offset": 100}).json() == []
    assert all("result" not in row for page in pages for row in page)


def test_history_combines_dataset_status_and_case_insensitive_sql_search(client, histories):
    datasets, rows = histories
    response = client.get(
        "/api/analyses",
        params={
            "dataset_id": datasets[1].id,
            "status": "error",
            "q": "  sum(revenue)  ",
            "limit": 100,
        },
    )
    assert response.status_code == 200
    expected = [
        row.id
        for row in reversed(rows)
        if row.dataset_id == datasets[1].id and row.status == "error"
    ]
    assert [row["id"] for row in response.json()] == expected
    assert expected


@pytest.mark.parametrize("term", ["问题", "revenue", "100%", "under_score", "/ literal"])
def test_history_keyword_matches_question_including_null_sql(client, histories, term):
    datasets, _ = histories
    response = client.get(
        "/api/analyses", params={"q": term, "limit": 100, "dataset_id": datasets[0].id}
    )
    assert response.status_code == 200
    assert len(response.json()) == 24


@pytest.mark.parametrize("term", ["100_", "under%score", "' OR 1=1 --", "no match"])
def test_history_search_is_literal_and_parameterized(client, histories, term):
    datasets, _ = histories
    assert (
        client.get("/api/analyses", params={"q": term, "dataset_id": datasets[0].id}).json() == []
    )


def test_history_blank_keyword_and_status_options(client, histories):
    datasets, _ = histories
    params = {"dataset_id": datasets[0].id}
    assert (
        client.get("/api/analyses", params={**params, "q": "   "}).json()
        == client.get("/api/analyses", params=params).json()
    )
    assert client.get("/api/analyses", params={**params, "status": "running"}).json() == []
    for status in ("success", "rejected", "error"):
        rows = client.get("/api/analyses", params={**params, "status": status}).json()
        assert rows and all(row["status"] == status for row in rows)


@pytest.mark.parametrize(
    "params",
    [
        {"status": "deleted"},
        {"q": "x" * 201},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"dataset_id": "bad-id"},
    ],
)
def test_history_rejects_invalid_filters(client, params):
    assert client.get("/api/analyses", params=params).status_code == 422
