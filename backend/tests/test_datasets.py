import io
import zipfile
from datetime import datetime

import duckdb
import pytest
from openpyxl import Workbook
from sqlalchemy.exc import SQLAlchemyError

from app.datasets import dataset_path


def xlsx_bytes(rows, sheets=1):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    for i in range(1, sheets):
        workbook.create_sheet(f"Sheet{i + 1}")
    stream = io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def upload(client, content, filename="sales.csv", **kwargs):
    return client.post("/api/datasets", files={"file": (filename, content)}, **kwargs)


def test_csv_real_import_profile_and_persistence(client, settings):
    response = upload(client, b"region,revenue,code\nEast,10,001\nWest,,002\nEast,30,003\n")
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["row_count"] == 3
    assert dataset["column_count"] == 3
    revenue = dataset["profile"]["fields"][1]
    assert revenue["missing"] == 1
    assert revenue["statistics"]["mean"] == 20
    assert dataset["profile"]["fields"][2]["type"] == "VARCHAR"
    assert dataset["profile"]["preview"][1]["revenue"] is None
    with duckdb.connect(str(dataset_path(settings, dataset["id"])), read_only=True) as db:
        assert db.execute('SELECT SUM(revenue) FROM "data"').fetchone()[0] == 40
    assert client.get(f"/api/datasets/{dataset['id']}").json() == dataset
    assert client.get("/api/datasets").json()[0]["id"] == dataset["id"]
    assert "profile" not in client.get("/api/datasets").json()[0]


def test_xlsx_real_import_and_sheet_selection(client):
    content = xlsx_bytes([["region", "revenue"], ["华东", 25], ["华北", 75]], sheets=2)
    response = upload(client, content, "sales.xlsx")
    assert response.status_code == 422
    assert response.json()["detail"]["sheets"] == ["Sheet", "Sheet2"]
    response = upload(client, content, "sales.xlsx", data={"sheet": "Sheet"})
    assert response.status_code == 201, response.text
    assert response.json()["profile"]["fields"][1]["statistics"]["mean"] == 50
    assert upload(client, content, "sales.xlsx", data={"sheet": "missing"}).status_code == 422


@pytest.mark.parametrize(
    "content,filename,status",
    [
        (b"", "empty.csv", 422),
        (b"name,value\n", "header.csv", 422),
        (b"name,Name\na,b\n", "duplicate.csv", 422),
        (b",value\na,b\n", "blank.csv", 422),
        (b"a,b\n1,2,3\n", "ragged.csv", 422),
        (b"\x00\xffbinary", "fake.csv", 422),
        (b"not an excel file", "fake.xlsx", 422),
        (b"a\n1", "file.xls", 415),
        (b"a\n1", "file.py", 415),
        (xlsx_bytes([["a"], ["=1+1"]]), "formula.xlsx", 422),
        (xlsx_bytes([["a", "a"], [1, 2]]), "duplicate.xlsx", 422),
    ],
)
def test_invalid_uploads_leave_no_duckdb(client, settings, content, filename, status):
    assert upload(client, content, filename).status_code == status
    assert not list(settings.data_dir.glob("datasets/*.duckdb"))


def test_size_rows_and_columns_limits(client, settings):
    settings.max_upload_bytes = 32
    assert upload(client, b"a\n" + b"x" * 40).status_code == 413
    settings.max_upload_bytes = 1024
    settings.max_dataset_rows = 2
    assert upload(client, b"a\n1\n2\n3\n").status_code == 422
    settings.max_upload_bytes = 16 * 1024
    assert upload(client, xlsx_bytes([["a"], [1], [2], [3]]), "too-many.xlsx").status_code == 422
    settings.max_dataset_columns = 1
    assert upload(client, b"a,b\n1,2\n").status_code == 422
    assert upload(client, xlsx_bytes([["a", "b"], [1, 2]]), "too-wide.xlsx").status_code == 422


def test_zip_masquerading_as_xlsx(client):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("arbitrary.txt", "hello")
    assert upload(client, stream.getvalue(), "fake.xlsx").status_code == 422


def test_utf8_bom_single_column_and_mixed_types(client):
    response = upload(client, "\ufeffvalue\n10\nunknown\n".encode())
    assert response.status_code == 201
    assert response.json()["profile"]["fields"][0]["type"] == "VARCHAR"


def test_native_excel_dates_remain_temporal(client):
    response = upload(
        client,
        xlsx_bytes([["date", "value"], [datetime(2026, 1, 1), 10], [datetime(2026, 2, 1), 20]]),
        "dates.xlsx",
    )
    assert response.status_code == 201
    assert "TIMESTAMP" in response.json()["profile"]["fields"][0]["type"]


def test_not_found_and_path_rejection(client):
    assert client.get("/api/datasets/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/datasets/not-a-uuid").status_code == 422


def test_failed_database_commit_removes_imported_file(client, settings, session, monkeypatch):
    def fail():
        raise SQLAlchemyError("private database details")

    monkeypatch.setattr(session, "commit", fail)
    response = upload(client, b"a,b\n1,2\n")
    assert response.status_code == 503
    assert "private" not in response.text
    assert not list(settings.data_dir.glob("datasets/*.duckdb"))


def test_actual_request_body_limit_without_content_length(client):
    # Streaming body has no trusted Content-Length; rejected before importing.
    def oversized():
        for _ in range(12):
            yield b"x" * (1024 * 1024)

    response = client.post(
        "/api/datasets",
        content=oversized(),
        headers={"Content-Type": "multipart/form-data; boundary=too-big"},
    )
    assert response.status_code == 413, response.text
