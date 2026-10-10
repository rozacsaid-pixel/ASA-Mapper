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


def test_option_suggestions_limited_to_parent(library):
    f = field("Is the undersigned a bank holding company?", widget="Radio", options=["Yes", "No"])
    out = Matcher(library).suggest_options(f, "asa_bhc_specialentities")
    assert out["Yes"][0].asa == "asa_yes_bhc_specialentities"
    assert out["No"][0].asa == "asa_no_bhc_specialentities"
    assert {s.asa for ss in out.values() for s in ss} <= {
        "asa_yes_bhc_specialentities", "asa_no_bhc_specialentities"}


def test_option_suggestions_unknown_parent(library):
    assert Matcher(library).suggest_options(field("x", widget="Radio", options=["A"]), "asa_nope") == {"A": []}
