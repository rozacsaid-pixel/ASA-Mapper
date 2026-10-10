"""Synthetic export and library builders. No client data."""
import io
import json
import zipfile
from pathlib import Path

import openpyxl

LIB_HEADER = [
    "Version", "Last update", "Section", "Field group", "Field meaning summary",
    "Field meaning full definition", "Keywords", "Field name", "Field option", "Type",
    "Available under field name", "Data type", "Input type in the form",
]


def lib_row(section, summary, name="", option="", kind="Field name", parent="", input_type="", group=""):
    return ["1.0", "2024-01-01", section, group, summary, "", "", name, option, kind, parent, "String", input_type]


LIB_ROWS = [
    lib_row("Investor type", "Investor Type", name="asa_investortype_investortype", input_type="Radio"),
    lib_row("Investor type", "Investor Type - Individual", option="asa_individual_investortype",
            kind="Field option", parent="asa_investortype_investortype"),
    lib_row("Investor type", "Investor Type - Entity", option="asa_entity_investortype",
            kind="Field option", parent="asa_investortype_investortype"),
    lib_row("General Information", "Full name", name="asa_fullname_investorname_generalinfo",
            input_type="Textbox", group="Investor name"),
    lib_row("Contact information", "Email address", name="asa_email_primarycontact_contactinfo",
            input_type="Textbox", group="Primary contact"),
    lib_row("Contact information", "Phone number", name="asa_phone_primarycontact_contactinfo",
            input_type="Textbox", group="Primary contact"),
    lib_row("Special Entities", "Bank holding company", name="asa_bhc_specialentities", input_type="Radio"),
    lib_row("Special Entities", "Yes", option="asa_yes_bhc_specialentities",
            kind="Field option", parent="asa_bhc_specialentities"),
    lib_row("Special Entities", "No", option="asa_no_bhc_specialentities",
            kind="Field option", parent="asa_bhc_specialentities"),
    lib_row("Special Entities", "Orphan option", option="asa_orphan_specialentities",
            kind="Field option", parent="asa_missing_parent"),
    lib_row("Broken", "Not an alias", name="notanalias"),
]


def make_library_xlsx(rows=None, tab="ASA List", header=None) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.title = "Overview"
    wb.active.append(["Introduction"])
    sheet = wb.create_sheet(tab)
    sheet.append([None] * len(LIB_HEADER))
    sheet.append(header or LIB_HEADER)
    for row in LIB_ROWS if rows is None else rows:
        sheet.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _node(name, title="", props=None):
    node = {"name": name, "type": "object" if props is not None else "string", "title": title}
    if props is not None:
        node["properties"] = props
    return node


def _opt(key, label, pdf=()):
    return [key, {"formattedText": f"<p>{label}</p>", "pdfMappings": list(pdf), "blueprintMetadataMapping": ""}]


SCHEMA = {"type": "object", "properties": [
    _node("hidden", "Hidden", [_node("sig", "Signature")]),
    _node("questionnaire", "Investor Questionnaire", [
        _node("part1", "Part I. General Information", [
            _node("investortype", "Radio"),
            _node("fullname", "Textbox"),
            _node("paragraph1"),
            _node("email1"),
            _node("phone1"),
            _node("commitment"),
        ]),
        _node("part2", "H. Identification of Special Entities", [_node("bank")]),
    ]),
    _node("logic", "Logic Support", [_node("split1"), _node("is_indi")]),
]}

UI = {
    "hidden": {"ui:widget": "Page"},
    "sig": {"ui:widget": "Signature"},
    "questionnaire": {"ui:widget": "Page"},
    "part1": {"ui:widget": "Group"},
    "part2": {"ui:widget": "Group"},
    "investortype": {"ui:widget": "Radio", "ui:formattedText": "<p>Investor type: <strong>*</strong></p>",
                     "ui:multipleOption": {"options": [_opt("individual", "Individual"), _opt("entity", "Entity")]}},
    "fullname": {"ui:widget": "TextBox", "ui:formattedText": "<p>Full Legal Name of Investor:﻿ *</p>",
                 "ui:pdfMapping": ["asa_fullname_investorname_generalinfo"]},
    "paragraph1": {"ui:widget": "Paragraph", "ui:formattedText": "<p><strong>Primary Contact</strong></p>"},
    "email1": {"ui:widget": "Email", "ui:formattedText": "<p>Email Address: *</p>"},
    "phone1": {"ui:widget": "Phone", "ui:formattedText": "<p>Phone Number:</p>"},
    "commitment": {"ui:widget": "Money", "ui:formattedText": "<p>Capital Commitment *</p>",
                   "ui:pdfMapping": ["asa_commitment_amount"]},
    "bank": {"ui:widget": "Radio",
             "ui:formattedText": '<p>Is the undersigned a bank holding company? <span data-title="note">FOOT_NOTE_HERE</span></p>',
             "ui:pdfMapping": ["asa_bhc_specialentities"],
             "ui:multipleOption": {"options": [_opt("Yes", "Yes", ["asa_yes_bhc_specialentities"]),
                                               _opt("No", "No", ["asa_no_bhc_specialentities"])]}},
    "logic": {"ui:widget": "Page"},
    "split1": {"ui:widget": "TextBox", "ui:formattedText": "For Splitting Rule"},
    "is_indi": {"ui:widget": "Radio", "ui:multipleOption": {"options": [_opt("yes", "yes"), _opt("no", "no")]}},
}

FORM_DATA = {"form": {"namespaceFormSchemaMap": {"main": {"schema": SCHEMA, "uiSchema": UI}}, "rules": []},
             "uploadedPdf": []}
RECORD_DATA = {"rData": {"7::abc": {"genericModelName": "Sample Fund - Subscription Booklet", "formData": [5, "x"]}}}


def write_export(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "form_data.json").write_text(json.dumps(FORM_DATA))
    (folder / "record_data.json").write_text(json.dumps(RECORD_DATA))
    return folder


def zip_export(folder: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in sorted(folder.iterdir()):
            zf.write(f, f"{folder.name}/{f.name}")
    return buf.getvalue()
