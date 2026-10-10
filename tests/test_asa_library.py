import json

import pytest

from asa_mapper.asa_library import LibraryError, load_library_xlsx, service_account_info
from tests.helpers import LIB_HEADER, LIB_ROWS, make_library_xlsx


def test_loads_names_and_options(library):
    assert len(library.names()) == 5
    assert [e.asa for e in library.options_under("asa_bhc_specialentities")] == [
        "asa_yes_bhc_specialentities", "asa_no_bhc_specialentities"]
    entry = library.get("asa_email_primarycontact_contactinfo")
    assert (entry.section, entry.group, entry.summary, entry.input_type) == (
        "Contact information", "Primary contact", "Email address", "Textbox")
    assert "asa_commitment_amount" not in library


def test_reports_issues(library):
    assert any("notanalias" in i for i in library.issues)
    assert any("asa_orphan_specialentities" in i and "asa_missing_parent" in i for i in library.issues)


def test_duplicate_alias_is_skipped():
    lib = load_library_xlsx(make_library_xlsx(LIB_ROWS + [LIB_ROWS[3]]))
    assert sum(e.asa == "asa_fullname_investorname_generalinfo" for e in lib.entries) == 1
    assert any("duplicate" in i for i in lib.issues)


def test_missing_tab():
    with pytest.raises(LibraryError, match="Tab 'ASA List' not found"):
        load_library_xlsx(make_library_xlsx(tab="Other"))


def test_missing_column():
    header = [h for h in LIB_HEADER if h != "Available under field name"] + [""]
    with pytest.raises(LibraryError, match="Available under field name"):
        load_library_xlsx(make_library_xlsx(header=header))


def test_not_an_xlsx():
    with pytest.raises(LibraryError):
        load_library_xlsx(b"nope")


def test_service_account_from_json_or_path(tmp_path):
    info = {"type": "service_account", "client_email": "x@y"}
    assert service_account_info(json.dumps(info)) == info
    path = tmp_path / "sa.json"
    path.write_text(json.dumps(info))
    assert service_account_info(str(path)) == info


def test_alias_words_are_part_of_matching_text(library):
    assert "yes bhc specialentities" in library.get("asa_yes_bhc_specialentities").text
