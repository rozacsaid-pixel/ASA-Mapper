from asa_mapper.models import PastMapping
from asa_mapper.store import Store


def test_library_roundtrip(tmp_path, library_xlsx):
    store = Store(tmp_path / "db.sqlite")
    assert store.load_library() is None
    store.save_library(library_xlsx, "upload")
    data, source, synced_at = store.load_library()
    assert data == library_xlsx and source == "upload" and synced_at


def test_sessions(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    sid = store.create_session("Form", {"fields": [], "suggestions": {}})
    session = store.get_session(sid)
    assert session["form_name"] == "Form" and session["decisions"] == {} and session["finished_at"] is None
    store.save_decisions(sid, {"k": {"status": "accepted", "asa": "asa_x"}})
    assert store.get_session(sid)["decisions"]["k"]["asa"] == "asa_x"
    store.finish_session(sid)
    assert store.get_session(sid)["finished_at"]
    assert store.get_session("missing") is None
    assert store.list_sessions()[0]["id"] == sid


def test_past_mappings_replaced_per_form(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    m = PastMapping(form="A", kind="field", label="L", path=["P"], headings=[], widget="TextBox", asa="asa_x")
    store.replace_past("A", [m, m])
    store.replace_past("A", [m])
    store.replace_past("B", [m])
    assert len(store.list_past()) == 2
    assert store.list_past()[0] == m
