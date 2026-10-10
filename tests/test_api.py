import pytest
from fastapi.testclient import TestClient

from asa_mapper.api import create_app
from asa_mapper.settings import Settings


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path / "data")))


def upload(client, path, name, data):
    return client.post(path, files={"file": (name, data)})


def start(client, name, export_zip, template=None, asa=None):
    from tests.helpers import make_template_xlsx
    files = {"file": (name, export_zip), "template": ("template.xlsx", template or make_template_xlsx())}
    if asa is not None:
        files["asa_file"] = ("asa.txt", asa)
    return client.post("/api/sessions", files=files)


def test_full_review_flow(client, library_xlsx, export_zip):
    assert client.get("/api/library").json()["loaded"] is False
    assert start(client, "e.zip", export_zip).status_code == 409

    lib = upload(client, "/api/library/upload", "lib.xlsx", library_xlsx).json()
    assert (lib["loaded"], lib["names"], lib["options"]) == (True, 5, 5)

    created = start(client, "Sample - Version 3.zip", export_zip).json()
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
    assert start(client, "x.zip", b"nope").status_code == 400
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


def test_session_with_system_asa_export_and_txt_download(client, library_xlsx, export_zip):
    upload(client, "/api/library/upload", "lib.xlsx", library_xlsx)
    asa_text = b"email1-->asa_email_primarycontact_contactinfo\nbank#No-->asa_no_bhc_specialentities\nghost-->asa_x\n"
    created = start(client, "e.zip", export_zip, asa=asa_text).json()
    assert created["existing_asa"] == 2 and created["asa_unmatched"] == ["ghost"]
    fields = {f["key"]: f for f in client.get(f"/api/sessions/{created['id']}").json()["review"]["fields"]}
    assert fields["email1"]["existing_asa"] == ["asa_email_primarycontact_contactinfo"]
    assert fields["fullname"]["existing_asa"] == []
    bad = start(client, "e.zip", export_zip, asa=b"oops")
    assert bad.status_code == 400
    client.put(f"/api/sessions/{created['id']}/decisions",
               json={"phone1": {"status": "changed", "asa": "asa_phone_primarycontact_contactinfo"}})
    txt = client.get(f"/api/sessions/{created['id']}/asa.txt")
    assert txt.status_code == 200 and txt.text == "phone1-->asa_phone_primarycontact_contactinfo\n"


def test_template_required_checked_and_filled(client, library_xlsx, export_zip):
    from tests.helpers import make_template_xlsx
    import io, openpyxl
    upload(client, "/api/library/upload", "lib.xlsx", library_xlsx)
    assert client.post("/api/sessions", files={"file": ("e.zip", export_zip)}).status_code == 422
    assert start(client, "e.zip", export_zip, template=library_xlsx).status_code == 400
    created = start(client, "e.zip", export_zip, template=make_template_xlsx(drop={"email1"}),
                    asa=b"phone1-->asa_phone_primarycontact_contactinfo\n").json()
    assert created["template"]["missing_in_template"] == ["email1"]
    sid = created["id"]
    client.put(f"/api/sessions/{sid}/decisions",
               json={"bank": {"status": "accepted", "asa": "asa_bhc_specialentities", "options": {}}})
    filled = client.get(f"/api/sessions/{sid}/template.xlsx")
    assert filled.status_code == 200 and filled.headers["x-asa-rows-filled"] == "2"
    ws = openpyxl.load_workbook(io.BytesIO(filled.content)).active
    asas = {r[1]: r[9] for r in ws.iter_rows(min_row=2, values_only=True) if r[1]}
    assert asas["bank"] == "asa_bhc_specialentities"
    assert asas["phone1"] == "asa_phone_primarycontact_contactinfo"   # unreviewed: kept from system export
    assert not asas["fullname"]
