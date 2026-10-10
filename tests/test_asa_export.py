import pytest

from asa_mapper.asa_export import AsaExportError, apply_asa_export, format_asa_export, parse_asa_export
from asa_mapper.export_parser import parse_export


def test_parse_lines():
    text = "bank-->asa_bhc_specialentities\n\nbank#Yes-->asa_yes_bhc_specialentities \r\n"
    assert parse_asa_export(text) == {"bank": "asa_bhc_specialentities", "bank#Yes": "asa_yes_bhc_specialentities"}


def test_parse_rejects_malformed_line():
    with pytest.raises(AsaExportError, match="line 2"):
        parse_asa_export("a-->asa_x\nnot a mapping\n")


def test_apply_replaces_existing_asa(export_dir):
    form = parse_export(export_dir)
    unknown = apply_asa_export(form, {"email1": "asa_email_primarycontact_contactinfo",
                                      "bank#No": "asa_no_bhc_specialentities", "missing": "asa_x"})
    fields = {f.key: f for f in form.fields}
    assert fields["email1"].existing_asa == ["asa_email_primarycontact_contactinfo"]
    assert fields["fullname"].existing_asa == []          # PDF link replaced by the system export
    assert [o.existing_asa for o in fields["bank"].options] == [[], ["asa_no_bhc_specialentities"]]
    assert unknown == ["missing"]


def test_format_accepted_decisions_only():
    decisions = {
        "bank": {"status": "accepted", "asa": "asa_bhc_specialentities",
                 "options": {"Yes": {"status": "changed", "asa": "asa_yes_bhc_specialentities"},
                             "No": {"status": "pending", "asa": ""}}},
        "email1": {"status": "none", "asa": ""},
        "phone1": {"status": "pending", "asa": "asa_phone_primarycontact_contactinfo"},
    }
    assert format_asa_export(decisions) == (
        "bank-->asa_bhc_specialentities\nbank#Yes-->asa_yes_bhc_specialentities\n")
