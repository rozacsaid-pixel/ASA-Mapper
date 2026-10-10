from asa_mapper.evaluate import evaluate
from asa_mapper.export_parser import parse_export
from asa_mapper.models import PastMapping
from asa_mapper.past_mappings import decisions_to_mappings, mappings_from_form


def test_mappings_from_form(export_dir, library):
    maps = mappings_from_form(parse_export(export_dir), library)
    kinds = {(m.kind, m.asa) for m in maps}
    assert ("field", "asa_fullname_investorname_generalinfo") in kinds
    assert ("field", "asa_bhc_specialentities") in kinds
    assert ("option", "asa_yes_bhc_specialentities") in kinds
    assert all(m.asa != "asa_commitment_amount" for m in maps)
    yes = next(m for m in maps if m.asa == "asa_yes_bhc_specialentities")
    assert yes.parent_asa == "asa_bhc_specialentities"
    assert yes.field_label.startswith("Is the undersigned")


def test_decisions_to_mappings(export_dir, library):
    form = parse_export(export_dir)
    review = {"fields": [f.to_dict() for f in form.fields], "suggestions": {}}
    decisions = {
        "phone1": {"status": "changed", "asa": "asa_phone_primarycontact_contactinfo"},
        "email1": {"status": "pending", "asa": ""},
        "bank": {"status": "accepted", "asa": "asa_bhc_specialentities",
                 "options": {"No": {"status": "accepted", "asa": "asa_no_bhc_specialentities"}}},
        "fullname": {"status": "accepted", "asa": "asa_not_in_library"},
    }
    maps = decisions_to_mappings("Form A", review, decisions, library)
    assert sorted((m.kind, m.asa) for m in maps) == [
        ("field", "asa_bhc_specialentities"),
        ("field", "asa_phone_primarycontact_contactinfo"),
        ("option", "asa_no_bhc_specialentities"),
    ]
    assert all(m.form == "Form A" for m in maps)


def test_evaluate_reports_accuracy(export_dir, library):
    report = evaluate(library, mappings_from_form(parse_export(export_dir), library))
    assert report["forms"] == 1
    assert report["fields"]["total"] == 2
    assert report["options"]["total"] == 2
    field_misses = [m for m in report["misses"] if m["kind"] == "field"]
    assert report["fields"]["top1"] + len(field_misses) == 2
    assert 0 <= report["fields"]["top1_rate"] <= 1


def test_evaluate_excludes_the_form_being_scored(library):
    own = PastMapping(form="A", kind="field", label="zzz", path=[], headings=[], widget="TextBox",
                      asa="asa_fullname_investorname_generalinfo")
    assert evaluate(library, [own])["fields"]["top1"] == 0
