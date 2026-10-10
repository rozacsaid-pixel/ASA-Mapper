import pytest
from fastapi.testclient import TestClient

from asa_mapper.api import create_app
from asa_mapper.settings import Settings


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path / "data")))


def upload(client, path, name, data):
    return client.post(path, files={"file": (name, data)})


def test_full_review_flow(client, library_xlsx, export_zip):
    assert client.get("/api/library").json()["loaded"] is False
    assert upload(client, "/api/sessions", "e.zip", export_zip).status_code == 409

    lib = upload(client, "/api/library/upload", "lib.xlsx", library_xlsx).json()
    assert (lib["loaded"], lib["names"], lib["options"]) == (True, 5, 5)

    created = upload(client, "/api/sessions", "Sample - Version 3.zip", export_zip).json()
    assert created["form_name"] == "Sample Fund - Subscription Booklet (Version 3)"
    sid = created["id"]
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["review"]["suggestions"]["phone1"]["field"][0]["asa"] == "asa_phone_primarycontact_contactinfo"

    decisions = {"bank": {"status": "accepted", "asa": "asa_bhc_specialentities",
                          "options": {"Yes": {"status": "accepted", "asa": "asa_yes_bhc_specialentities"}}}}
    assert client.put(f"/api/sessions/{sid}/decisions", json=decisions).status_code == 200
    saved = client.get(f"/api/sessions/{sid}").json()["decisions"]["bank"]
    assert saved["at"] and saved["asa"] == "asa_bhc_specialentities"

    options = client.post(f"/api/sessions/{sid}/options",
                          json={"field_key": "bank", "parent_asa": "asa_bhc_specialentities"}).json()
    assert options["Yes"][0]["asa"] == "asa_yes_bhc_specialentities"

    sheet = client.get(f"/api/sessions/{sid}/sheet.xlsx")
    assert sheet.status_code == 200 and sheet.content[:2] == b"PK"

    assert client.post(f"/api/sessions/{sid}/finish").json()["saved"] == 2
    accuracy = client.get("/api/accuracy").json()
    assert accuracy["fields"]["total"] == 1 and accuracy["options"]["total"] == 1
    assert client.get("/api/search", params={"q": "phone"}).json()[0]["asa"] == "asa_phone_primarycontact_contactinfo"
    assert client.get("/api/sessions").json()[0]["id"] == sid


def test_bad_uploads(client, library_xlsx):
    assert upload(client, "/api/library/upload", "x.xlsx", b"nope").status_code == 400
    upload(client, "/api/library/upload", "lib.xlsx", library_xlsx)
    assert upload(client, "/api/sessions", "x.zip", b"nope").status_code == 400
    assert client.get("/api/sessions/missing").status_code == 404


def test_refresh_without_google(client):
    assert client.post("/api/library/refresh").status_code == 503


def test_index_served(client):
    assert "ASA Mapper" in client.get("/").text


def test_sign_in_required_when_configured(tmp_path):
    settings = Settings(data_dir=tmp_path, google_client_id="id", google_client_secret="secret",
                        session_secret="s" * 32)
    client = TestClient(create_app(settings))
    assert client.get("/api/library").status_code == 401
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307) and response.headers["location"].endswith("/login")
    assert client.get("/healthz").status_code == 200


def test_search_matches_all_words_and_prefers_parent(client, library_xlsx):
    upload(client, "/api/library/upload", "lib.xlsx", library_xlsx)
    assert [r["asa"] for r in client.get("/api/search", params={"q": "primary phone"}).json()] == [
        "asa_phone_primarycontact_contactinfo"]
    rows = client.get("/api/search", params={"kind": "option", "parent": "asa_bhc_specialentities"}).json()
    assert [r["asa"] for r in rows[:2]] == ["asa_yes_bhc_specialentities", "asa_no_bhc_specialentities"]
    assert "asa_individual_investortype" in [r["asa"] for r in rows]
