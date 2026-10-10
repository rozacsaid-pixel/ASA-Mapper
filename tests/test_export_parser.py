import pytest

from asa_mapper.export_parser import ExportError, clean_text, form_label, parse_export


def by_key(form):
    return {f.key: f for f in form.fields}


def test_folder_and_zip_parse_the_same(export_dir, export_zip):
    a, b = parse_export(export_dir), parse_export(export_zip)
    assert a.name == b.name == "Sample Fund - Subscription Booklet"
    assert [f.key for f in a.fields] == [f.key for f in b.fields]


def test_field_details(export_dir):
    f = by_key(parse_export(export_dir))
    assert f["fullname"].label == "Full Legal Name of Investor: *"
    assert f["fullname"].path == ["Investor Questionnaire", "Part I. General Information"]
    assert f["fullname"].existing_asa == ["asa_fullname_investorname_generalinfo"]
    assert f["email1"].headings == ["Primary Contact"]
    assert f["bank"].label == "Is the undersigned a bank holding company?"
    assert [(o.key, o.label, o.existing_asa) for o in f["bank"].options] == [
        ("Yes", "Yes", ["asa_yes_bhc_specialentities"]),
        ("No", "No", ["asa_no_bhc_specialentities"]),
    ]


def test_skip_reasons(export_dir):
    f = by_key(parse_export(export_dir))
    assert f["sig"].skip_reason == "Signature field"
    assert f["split1"].skip_reason == "logic helper page"
    assert f["is_indi"].skip_reason == "logic helper page"
    assert f["email1"].skip_reason is None
    assert "paragraph1" not in f


def test_rejects_folder_without_form_data(tmp_path):
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(ExportError, match="form_data.json"):
        parse_export(tmp_path)


def test_rejects_bad_zip():
    with pytest.raises(ExportError):
        parse_export(b"not a zip")


def test_clean_text_strips_html_and_footnotes():
    assert clean_text('<p>A&nbsp;<b>B</b> <span data-title="x">FOOT_NOTE_HERE</span></p>') == "A B"


def test_form_label_adds_version():
    assert form_label("Fund", "Fund - Version 36.zip") == "Fund (Version 36)"
    assert form_label("Fund", "export.zip") == "Fund"


def test_instruction_and_consent_checkboxes_are_skipped():
    from asa_mapper.export_parser import skip_reason
    from asa_mapper.models import FormOption

    def box(*labels, widget="MultipleCheckbox"):
        return [FormOption(key=str(i), label=l) for i, l in enumerate(labels)]

    reason = "instruction/consent checkbox"
    assert skip_reason("multiplecheckbox33", "MultipleCheckbox", "Multiplecheckbox 24", [],
                       box("Check this box if you have a secondary contact to receive the information")) == reason
    assert skip_reason("checkif3", "MultipleCheckbox", "Check if", [], box("Check if you wish to add contacts")) == reason
    assert skip_reason("h_confirm", "MultipleCheckbox", "Multiplecheckbox", [],
                       box("I confirm that I have read, understand, and agree to the above")) == reason
    assert skip_reason("x", "MultipleCheckbox", "x", [], box("I hereby acknowledge the terms")) == reason
    # real data points stay
    assert skip_reason("radio", "MultipleCheckbox", "Or, the undersigned represents", [],
                       box("The undersigned is NOT an accredited investor.")) is None
    assert skip_reason("mix", "MultipleCheckbox", "x", [], box("Check this box if...", "A bank")) is None
    assert skip_reason("r", "Radio", "Check this box", [], box("Check this box if ...")) is None
