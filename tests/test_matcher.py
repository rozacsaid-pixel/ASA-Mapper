from asa_mapper.matcher import Matcher, input_family, widget_family
from asa_mapper.models import FormField, FormOption, PastMapping


def field(label, widget="TextBox", path=None, headings=None, options=()):
    return FormField(key="k", widget=widget, label=label, path=path or [], headings=headings or [],
                     options=[FormOption(key=o, label=o) for o in options])


def test_families():
    assert widget_family("Radio") == "radio"
    assert widget_family("MultipleCheckbox") == "checkbox"
    assert widget_family("Email") == "text"
    assert input_family("Dropdown\n(Country)") == "text"
    assert input_family("multipleChecklist") == "checkbox"
    assert input_family("") == "any"


def test_suggests_by_text_and_heading(library):
    s = Matcher(library).suggest_field(field("Phone Number:", widget="Phone", headings=["Primary Contact"]))
    assert s[0].asa == "asa_phone_primarycontact_contactinfo"
    assert s[0].confidence in {"High", "Medium"}
    assert len(s) <= 3


def test_type_mismatch_is_penalised(library):
    m = Matcher(library)
    radio = m.suggest_field(field("Investor type", widget="Radio"))[0]
    as_text = [s for s in m.suggest_field(field("Investor type"), top_n=10)
               if s.asa == "asa_investortype_investortype"][0]
    assert radio.asa == "asa_investortype_investortype"
    assert as_text.score < radio.score
    assert "type mismatch" in as_text.reason


def test_past_mapping_boosts(library):
    query = field("Contact phone", widget="Phone", path=["Contacts"])
    past = [PastMapping(form="Other", kind="field", label="Contact phone", path=["Contacts"], headings=[],
                        widget="Phone", asa="asa_email_primarycontact_contactinfo")]
    assert Matcher(library).suggest_field(query)[0].asa == "asa_phone_primarycontact_contactinfo"
    boosted = Matcher(library, past).suggest_field(query)
    assert boosted[0].asa == "asa_email_primarycontact_contactinfo"
    assert "seen before in 'Other'" in boosted[0].reason


def test_no_overlap_gives_no_suggestions(library):
    assert Matcher(library).suggest_field(field("zzz qqq")) == []


def test_option_suggestions_rank_parent_options_first(library):
    f = field("Is the undersigned a bank holding company?", widget="Radio", options=["Yes", "No"])
    out = Matcher(library).suggest_options(f, "asa_bhc_specialentities")
    assert out["Yes"][0].asa == "asa_yes_bhc_specialentities"
    assert out["No"][0].asa == "asa_no_bhc_specialentities"
    assert "under field ASA" in out["Yes"][0].reason


def test_option_suggestions_come_from_whole_library(library):
    f = field("Investor type", widget="MultipleCheckbox", options=["Entity"])
    assert Matcher(library).suggest_options(f, "")["Entity"][0].asa == "asa_entity_investortype"


def test_option_past_mapping_needs_same_field_wording(library):
    f = field("Is the undersigned a bank holding company?", widget="Radio", options=["Unsure"])
    past = [PastMapping(form="Old", kind="option", label="Unsure", path=[], headings=[], widget="Radio",
                        asa="asa_no_bhc_specialentities", parent_asa="asa_bhc_specialentities",
                        field_label="Is the undersigned a bank holding company?")]
    top = Matcher(library, past).suggest_options(f, "asa_bhc_specialentities")["Unsure"][0]
    assert top.asa == "asa_no_bhc_specialentities" and "seen before in 'Old'" in top.reason
    other = field("Something else entirely?", widget="Radio", options=["Unsure"])
    assert "seen before" not in Matcher(library, past).suggest_options(other, "asa_bhc_specialentities")["Unsure"][0].reason


def test_yes_no_polarity(library):
    radio = field("Is the undersigned a bank holding company?", widget="Radio", options=["Yes", "No"])
    out = Matcher(library).suggest_options(radio, "asa_bhc_specialentities", top_n=10)
    assert "asa_no_bhc_specialentities" not in [s.asa for s in out["Yes"]]
    assert "asa_yes_bhc_specialentities" not in [s.asa for s in out["No"]]
    box = field("Bank holding company", widget="MultipleCheckbox", options=["Bank holding company"])
    assert all(not s.asa.startswith("asa_no_")
               for s in Matcher(library).suggest_options(box, "", top_n=10)["Bank holding company"])


def test_field_key_words_help_generic_labels(library):
    assert Matcher(library).suggest_field(
        FormField(key="phone_contact1", widget="Phone", label="Customformat"))[0].asa == "asa_phone_primarycontact_contactinfo"

