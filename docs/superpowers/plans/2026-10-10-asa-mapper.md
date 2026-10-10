# ASA Mapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A web app that reads an Anduin form export, suggests ASAs (Anduin Standard Aliases) from the ASA Library for each field and option, lets a DS review them, and downloads a review sheet.

**Architecture:** A Python package `asa_mapper` with small, separately tested modules (parser, library loader, matcher, store, evaluation, exporter) behind a FastAPI app that serves a plain HTML/JS review page. Data lives in one SQLite file. No AI in version 1.

**Tech Stack:** Python 3.9+, FastAPI, scikit-learn (TF-IDF), openpyxl, SQLite, google-auth (Sheet sync), Authlib (optional Google sign-in), vanilla JS/CSS.

Spec: `docs/superpowers/specs/2026-10-10-asa-mapper-design.md`

## Global Constraints

- Python 3.9 compatible: `from __future__ import annotations` in every module; no `X | Y` types in FastAPI endpoint signatures; no `match`.
- Library source is the **"ASA List"** tab only (`ASA_SHEET_TAB`, default `ASA List`); header row found automatically in the first 10 rows.
- Field-level matches use Field name ASAs; Radio/MultipleCheckbox options use Field option ASAs whose `Available under field name` equals the field's ASA.
- Client exports never go into git: real exports live in `fixtures/private/` (git-ignored); committed tests use the synthetic export in `tests/helpers.py`.
- Uploaded exports are parsed in memory and not written to disk; only extracted field text is stored.
- Accuracy baseline to beat (first choice, plain text, v36): fields 63%, options 66%.

## File structure

```
asa-mapper/
  requirements.txt
  pytest.ini
  README.md
  Dockerfile, docker-compose.yml, .env.example
  asa_mapper/
    __init__.py
    models.py          dataclasses shared by all modules
    export_parser.py   export (.zip / folder / bytes) -> ParsedForm
    asa_library.py     xlsx / Google Sheet -> Library
    matcher.py         FormField -> ranked Suggestions
    past_mappings.py   existing/accepted ASAs -> PastMapping records
    evaluate.py        leave-one-form-out accuracy report
    store.py           SQLite persistence
    service.py         build_review(): suggestions + flags per field
    exporter.py        review sheet .xlsx
    settings.py        environment configuration
    auth.py            optional Google sign-in
    api.py             FastAPI app factory
    cli.py             library / seed / evaluate commands
    static/index.html, app.js, style.css
  tests/
    helpers.py         synthetic export + library builders
    conftest.py        fixtures
    test_*.py
```

---

### Task 1: Project scaffold, models, export parser

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `asa_mapper/__init__.py`, `asa_mapper/models.py`, `asa_mapper/export_parser.py`
- Test: `tests/__init__.py`, `tests/helpers.py`, `tests/conftest.py`, `tests/test_export_parser.py`

**Interfaces:**
- Produces: `parse_export(source: str | Path | bytes, fallback_name: str = "Untitled form") -> ParsedForm`; `form_label(name: str, filename: str) -> str`; `clean_text(raw: str) -> str`; `ExportError(ValueError)`.
- Produces models: `FormOption(key, label, existing_asa)`, `FormField(key, widget, label, path, headings, options, existing_asa, hidden, skip_reason)` with `to_dict()` / `from_dict()`, `ParsedForm(name, fields)`, `AsaEntry`, `Library`, `Suggestion`, `PastMapping` (used by later tasks).

- [ ] **Step 1: Scaffold and virtual environment**

```text file=requirements.txt
fastapi==0.115.6
uvicorn==0.32.1
python-multipart==0.0.20
openpyxl==3.1.5
scikit-learn==1.6.1
google-auth==2.37.0
requests==2.32.3
authlib==1.4.0
itsdangerous==2.2.0
httpx==0.28.1
pytest==8.3.4
```

```ini file=pytest.ini
[pytest]
testpaths = tests
addopts = -q
```

```python file=asa_mapper/__init__.py
"""ASA Mapper: suggest Anduin Standard Aliases for form fields."""
```

```python file=tests/__init__.py
```

Run: `python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt`
Expected: installs without errors.

- [ ] **Step 2: Write the shared models**

```python file=asa_mapper/models.py
"""Data types shared across ASA Mapper modules."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class FormOption:
    key: str
    label: str
    existing_asa: List[str] = field(default_factory=list)


@dataclass
class FormField:
    key: str
    widget: str
    label: str
    path: List[str] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    options: List[FormOption] = field(default_factory=list)
    existing_asa: List[str] = field(default_factory=list)
    hidden: bool = False
    skip_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FormField":
        data = dict(data)
        data["options"] = [FormOption(**o) for o in data.get("options", [])]
        return cls(**data)


@dataclass
class ParsedForm:
    name: str
    fields: List[FormField]


@dataclass
class AsaEntry:
    asa: str
    kind: str  # "name" or "option"
    section: str = ""
    group: str = ""
    summary: str = ""
    definition: str = ""
    keywords: str = ""
    parent: str = ""  # options only: the field name ASA they belong to
    input_type: str = ""

    @property
    def text(self) -> str:
        return " ".join([self.section, self.group, self.summary, self.definition, self.keywords])


@dataclass
class Library:
    entries: List[AsaEntry]
    issues: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_asa = {e.asa: e for e in self.entries}

    def get(self, asa: str) -> Optional[AsaEntry]:
        return self._by_asa.get(asa)

    def __contains__(self, asa: str) -> bool:
        return asa in self._by_asa

    def names(self) -> List[AsaEntry]:
        return [e for e in self.entries if e.kind == "name"]

    def options_under(self, parent: str) -> List[AsaEntry]:
        return [e for e in self.entries if e.kind == "option" and e.parent == parent]


@dataclass
class Suggestion:
    asa: str
    score: float
    confidence: str  # "High", "Medium" or "Low"
    reason: str = ""


@dataclass
class PastMapping:
    form: str
    kind: str  # "field" or "option"
    label: str
    path: List[str]
    headings: List[str]
    widget: str
    asa: str
    parent_asa: str = ""
    field_label: str = ""
    option_labels: List[str] = field(default_factory=list)
```

- [ ] **Step 3: Write test helpers and fixtures**

```python file=tests/helpers.py
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
```

```python file=tests/conftest.py
import pytest

from tests.helpers import make_library_xlsx, write_export, zip_export


@pytest.fixture
def export_dir(tmp_path):
    return write_export(tmp_path / "Sample Export - Version 3")


@pytest.fixture
def export_zip(export_dir):
    return zip_export(export_dir)


@pytest.fixture
def library_xlsx():
    return make_library_xlsx()


@pytest.fixture
def library(library_xlsx):
    from asa_mapper.asa_library import load_library_xlsx
    return load_library_xlsx(library_xlsx)
```

- [ ] **Step 4: Write the failing parser tests**

```python file=tests/test_export_parser.py
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
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_export_parser.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'asa_mapper.export_parser'`

- [ ] **Step 6: Implement the parser**

```python file=asa_mapper/export_parser.py
"""Read an Anduin form export (.zip, folder or zip bytes) into FormFields."""
from __future__ import annotations

import html
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Union

from .models import FormField, FormOption, ParsedForm

LAYOUT_WIDGETS = {"Group", "Paragraph", "Header", "Page", "Pdf"}
HEADING_WIDGETS = {"Paragraph", "Header"}
SKIP_WIDGETS = {"Signature", "File", "FileGroup"}
HELPER_KEY = re.compile(r"^(is_|split\d+$|field_below$|radio_uncheck$|shortcut$|for_investment_entity$)")
HELPER_LABELS = ("for mapping", "combine all address fields", "for splitting rule")
LOGIC_PAGES = {"logic support"}
MAX_HEADING_LEN = 150
HEADINGS_KEPT = 2


class ExportError(ValueError):
    """The upload is not a readable form export."""


def clean_text(raw: str) -> str:
    text = re.sub(r"<span data-title=.*?</span>", " ", raw or "", flags=re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = text.replace("﻿", "").replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def form_label(name: str, filename: str) -> str:
    match = re.search(r"Version\s*\d+", filename or "", flags=re.I)
    if match and match.group(0).lower() not in name.lower():
        return f"{name} ({match.group(0)})"
    return name


def parse_export(source: Union[str, Path, bytes], fallback_name: str = "Untitled form") -> ParsedForm:
    try:
        files = _read_json_files(source)
    except zipfile.BadZipFile as exc:
        raise ExportError("The file is not a valid zip.") from exc
    if "form_data.json" not in files:
        raise ExportError("form_data.json not found in the export.")
    try:
        data = json.loads(files["form_data.json"])
        main = data["form"]["namespaceFormSchemaMap"]["main"]
        schema, ui = main["schema"], main["uiSchema"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ExportError("form_data.json does not have the expected structure.") from exc

    fields: List[FormField] = []
    headings: List[str] = []

    def walk(node: dict, path: List[str]) -> None:
        key = node.get("name") or ""
        title = node.get("title") or ""
        u = ui.get(key, {}) if key else {}
        widget = u.get("ui:widget")
        if widget in ("Page", "Group") and title:
            path = path + [title]
        if widget in HEADING_WIDGETS:
            text = clean_text(u.get("ui:formattedText")) or clean_text(u.get("ui:heading"))
            if text and len(text) <= MAX_HEADING_LEN:
                headings.append(text)
        elif widget and widget not in LAYOUT_WIDGETS:
            fields.append(_make_field(key, widget, title, u, path, headings[-HEADINGS_KEPT:]))
        for child in node.get("properties") or []:
            walk(child, path)

    for top in schema.get("properties") or []:
        walk(top, [])
    return ParsedForm(name=_form_name(files) or fallback_name, fields=fields)


def _read_json_files(source: Union[str, Path, bytes]) -> Dict[str, bytes]:
    if isinstance(source, (bytes, bytearray)):
        return _json_from_zip(zipfile.ZipFile(io.BytesIO(source)))
    path = Path(source)
    if path.is_dir():
        return {f.name: f.read_bytes() for f in path.glob("*.json")}
    if path.is_file() and zipfile.is_zipfile(path):
        return _json_from_zip(zipfile.ZipFile(path))
    raise ExportError(f"Not a folder or zip file: {path}")


def _json_from_zip(zf: zipfile.ZipFile) -> Dict[str, bytes]:
    out = {}
    for info in zf.infolist():
        if info.filename.startswith("__MACOSX") or not info.filename.endswith(".json"):
            continue
        out[info.filename.rsplit("/", 1)[-1]] = zf.read(info)
    return out


def _form_name(files: Dict[str, bytes]) -> str:
    try:
        records = json.loads(files.get("record_data.json", b"{}")).get("rData", {})
    except ValueError:
        return ""
    for record in records.values():
        if isinstance(record, dict) and "formData" in record and record.get("genericModelName"):
            return str(record["genericModelName"])
    return ""


def _make_field(key: str, widget: str, title: str, u: dict, path: List[str], headings: List[str]) -> FormField:
    label = clean_text(u.get("ui:formattedText")) or title
    options = []
    for opt_key, meta in (u.get("ui:multipleOption") or {}).get("options", []):
        meta = meta or {}
        options.append(FormOption(
            key=str(opt_key),
            label=clean_text(meta.get("formattedText")) or str(opt_key),
            existing_asa=[p for p in meta.get("pdfMappings") or [] if p.startswith("asa_")],
        ))
    existing = [p for p in u.get("ui:pdfMapping") or [] if p.startswith("asa_")]
    if key.startswith("asa_") and key not in existing:
        existing.insert(0, key)
    return FormField(
        key=key, widget=widget, label=label, path=list(path), headings=list(headings),
        options=options, existing_asa=existing, hidden=bool(u.get("ui:invisible")),
        skip_reason=_skip_reason(key, widget, label, path),
    )


def _skip_reason(key: str, widget: str, label: str, path: List[str]):
    if widget in SKIP_WIDGETS:
        return f"{widget} field"
    if any(p.strip().lower() in LOGIC_PAGES for p in path):
        return "logic helper page"
    if HELPER_KEY.match(key):
        return "logic helper field"
    if any(h in label.lower() for h in HELPER_LABELS):
        return "mapping helper field"
    return None
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_export_parser.py`
Expected: 7 passed

- [ ] **Step 8: Commit**

```bash
git add requirements.txt pytest.ini asa_mapper tests
git commit -m "feat: parse Anduin form exports into fields"
```

---

### Task 2: ASA Library loader (xlsx + Google Sheet)

**Files:**
- Create: `asa_mapper/asa_library.py`
- Test: `tests/test_asa_library.py`

**Interfaces:**
- Consumes: `AsaEntry`, `Library` from Task 1.
- Produces: `load_library_xlsx(data: bytes, tab: str = "ASA List") -> Library`; `fetch_google_sheet_xlsx(sheet_id: str, service_account: str) -> bytes`; `service_account_info(value: str) -> dict`; `LibraryError(ValueError)`; `XLSX_MIME`.

- [ ] **Step 1: Write the failing tests**

```python file=tests/test_asa_library.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_asa_library.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'asa_mapper.asa_library'`

- [ ] **Step 3: Implement the loader**

```python file=asa_mapper/asa_library.py
"""Load the ASA Library ("ASA List" tab) from xlsx bytes or Google Sheets."""
from __future__ import annotations

import io
import json
from pathlib import Path

import openpyxl

from .models import AsaEntry, Library

DEFAULT_TAB = "ASA List"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
REQUIRED_COLUMNS = ["Section", "Field meaning summary", "Field name", "Field option", "Type",
                    "Available under field name"]
OPTIONAL_COLUMNS = ["Field group", "Field meaning full definition", "Keywords", "Input type in the form"]
KINDS = {"field name": "name", "field option": "option"}


class LibraryError(ValueError):
    """The library file cannot be used."""


def _s(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def load_library_xlsx(data: bytes, tab: str = DEFAULT_TAB) -> Library:
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several unrelated types for bad files
        raise LibraryError("The library file is not a readable .xlsx.") from exc
    if tab not in wb.sheetnames:
        raise LibraryError(f"Tab '{tab}' not found. Tabs: {', '.join(wb.sheetnames)}")
    rows = list(wb[tab].iter_rows(values_only=True))

    header_idx = None
    for i, row in enumerate(rows[:10]):
        cells = [_s(c) for c in row or ()]
        if "Field name" in cells and "Type" in cells:
            header_idx = i
            break
    if header_idx is None:
        raise LibraryError("Header row with 'Field name' and 'Type' not found in the first 10 rows.")
    header = [_s(c) for c in rows[header_idx]]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise LibraryError("Missing columns: " + ", ".join(missing))
    col = {name: header.index(name) for name in REQUIRED_COLUMNS + OPTIONAL_COLUMNS if name in header}

    def get(row, name):
        i = col.get(name)
        return _s(row[i]) if i is not None and i < len(row) else ""

    entries, issues, seen = [], [], set()
    for number, row in enumerate(rows[header_idx + 1:], start=header_idx + 2):
        if not row or not any(row):
            continue
        kind = KINDS.get(get(row, "Type").lower())
        asa = get(row, "Field name" if kind == "name" else "Field option") if kind else ""
        if not kind or not asa.startswith("asa_"):
            issues.append(f"Row {number}: skipped (Type '{get(row, 'Type')}', alias '{asa}')")
            continue
        if asa in seen:
            issues.append(f"Row {number}: duplicate {asa} skipped")
            continue
        seen.add(asa)
        entries.append(AsaEntry(
            asa=asa, kind=kind, section=get(row, "Section"), group=get(row, "Field group"),
            summary=get(row, "Field meaning summary"), definition=get(row, "Field meaning full definition"),
            keywords=get(row, "Keywords"),
            parent=get(row, "Available under field name") if kind == "option" else "",
            input_type=get(row, "Input type in the form"),
        ))

    names = {e.asa for e in entries if e.kind == "name"}
    for e in entries:
        if e.kind == "option" and not e.parent:
            issues.append(f"{e.asa}: option has no parent field name")
        elif e.kind == "option" and e.parent not in names:
            issues.append(f"{e.asa}: parent {e.parent} not in library")
    return Library(entries=entries, issues=issues)


def service_account_info(value: str) -> dict:
    value = value.strip()
    if value.startswith("{"):
        return json.loads(value)
    return json.loads(Path(value).read_text())


def fetch_google_sheet_xlsx(sheet_id: str, service_account: str) -> bytes:
    """Download the whole sheet as .xlsx through the Drive API (sheet must be shared with the account)."""
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account as sa

    creds = sa.Credentials.from_service_account_info(
        service_account_info(service_account),
        scopes=["https://www.googleapis.com/auth/drive.readonly"])
    response = AuthorizedSession(creds).get(
        f"https://www.googleapis.com/drive/v3/files/{sheet_id}/export",
        params={"mimeType": XLSX_MIME}, timeout=60)
    response.raise_for_status()
    return response.content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_asa_library.py`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add asa_mapper/asa_library.py tests/test_asa_library.py
git commit -m "feat: load ASA Library from xlsx or Google Sheets"
```

---

### Task 3: Matcher

**Files:**
- Create: `asa_mapper/matcher.py`
- Test: `tests/test_matcher.py`

**Interfaces:**
- Consumes: `Library`, `FormField`, `FormOption`, `PastMapping`, `Suggestion`.
- Produces: `Matcher(library: Library, past: Iterable[PastMapping] = ())` with `suggest_field(field: FormField, top_n: int = 3) -> List[Suggestion]` and `suggest_options(field: FormField, parent_asa: str, top_n: int = 3) -> Dict[str, List[Suggestion]]` (keyed by option key); helpers `widget_family(widget) -> str`, `input_family(input_type) -> str`.

- [ ] **Step 1: Write the failing tests**

```python file=tests/test_matcher.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_matcher.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'asa_mapper.matcher'`

- [ ] **Step 3: Implement the matcher**

```python file=asa_mapper/matcher.py
"""Rank ASA candidates for form fields and options (version 1: no AI)."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Set

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .models import FormField, Library, PastMapping, Suggestion

TOP_N = 3
TYPE_MISMATCH_FACTOR = 0.5
PAST_LABEL_MIN_RATIO = 0.9
PAST_BOOST_BASE = 0.3
PAST_BOOST_PATH = 0.4
HIGH = 0.45
MEDIUM = 0.25
HIGH_MARGIN = 0.1


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def widget_family(widget: str) -> str:
    if widget == "Radio":
        return "radio"
    if widget == "MultipleCheckbox":
        return "checkbox"
    return "text"


def input_family(input_type: str) -> str:
    t = (input_type or "").lower()
    if not t:
        return "any"
    if "radio" in t:
        return "radio"
    if "checkbox" in t or "checklist" in t:
        return "checkbox"
    return "text"


def field_query(field: FormField) -> str:
    options = " ".join(o.label for o in field.options)
    return " ".join([field.label, field.label, " ".join(field.headings), " ".join(field.path), options])


def _label_match(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= PAST_LABEL_MIN_RATIO


def _path_similarity(a: List[str], b: List[str]) -> float:
    sa, sb = set(norm(" ".join(a)).split()), set(norm(" ".join(b)).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _confidence(score: float, margin: float, past_hit: bool) -> str:
    if score >= HIGH and (past_hit or margin >= HIGH_MARGIN):
        return "High"
    if score >= MEDIUM:
        return "Medium"
    return "Low"


def _rank(scores: Dict[str, float], reasons: Dict[str, str], top_n: int, past_hits: Set[str]) -> List[Suggestion]:
    ordered = [kv for kv in sorted(scores.items(), key=lambda kv: kv[1], reverse=True) if kv[1] > 0][:top_n]
    out = []
    for i, (asa, score) in enumerate(ordered):
        margin = score - ordered[i + 1][1] if i == 0 and len(ordered) > 1 else (score if i == 0 else 0.0)
        out.append(Suggestion(asa=asa, score=round(score, 4),
                              confidence=_confidence(score, margin, asa in past_hits), reason=reasons[asa]))
    return out


class Matcher:
    def __init__(self, library: Library, past: Iterable[PastMapping] = ()):
        self.library = library
        self.past = list(past)
        self._names = library.names()
        self._vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
        self._vec.fit([e.text for e in library.entries] or ["empty"])
        self._name_matrix = self._vec.transform([e.text for e in self._names]) if self._names else None

    def suggest_field(self, field: FormField, top_n: int = TOP_N) -> List[Suggestion]:
        if self._name_matrix is None:
            return []
        sims = cosine_similarity(self._vec.transform([field_query(field)]), self._name_matrix)[0]
        family = widget_family(field.widget)
        scores: Dict[str, float] = {}
        reasons: Dict[str, str] = {}
        for entry, sim in zip(self._names, sims):
            mismatch = input_family(entry.input_type) not in ("any", family)
            scores[entry.asa] = float(sim) * (TYPE_MISMATCH_FACTOR if mismatch else 1.0)
            reasons[entry.asa] = f"text match {sim:.2f}" + (", type mismatch" if mismatch else "")

        boosts: Dict[str, float] = {}
        forms: Dict[str, str] = {}
        for pm in self.past:
            if (pm.kind == "field" and pm.asa in scores and widget_family(pm.widget) == family
                    and _label_match(pm.label, field.label)):
                boost = PAST_BOOST_BASE + PAST_BOOST_PATH * _path_similarity(
                    pm.path + pm.headings, field.path + field.headings)
                if boost > boosts.get(pm.asa, 0.0):
                    boosts[pm.asa], forms[pm.asa] = boost, pm.form
        for asa, boost in boosts.items():
            scores[asa] += boost
            reasons[asa] += f", seen before in '{forms[asa]}'"
        return _rank(scores, reasons, top_n, set(boosts))

    def suggest_options(self, field: FormField, parent_asa: str, top_n: int = TOP_N) -> Dict[str, List[Suggestion]]:
        candidates = self.library.options_under(parent_asa)
        if not candidates:
            return {o.key: [] for o in field.options}
        matrix = self._vec.transform([e.text for e in candidates])
        out: Dict[str, List[Suggestion]] = {}
        for opt in field.options:
            query = " ".join([opt.label, opt.label, field.label])
            sims = cosine_similarity(self._vec.transform([query]), matrix)[0]
            scores = {e.asa: float(s) for e, s in zip(candidates, sims)}
            reasons = {e.asa: f"text match {s:.2f}" for e, s in zip(candidates, sims)}
            hits: Set[str] = set()
            for pm in self.past:
                if (pm.kind == "option" and pm.parent_asa == parent_asa and pm.asa in scores
                        and pm.asa not in hits and _label_match(pm.label, opt.label)):
                    hits.add(pm.asa)
                    scores[pm.asa] += PAST_BOOST_BASE + PAST_BOOST_PATH
                    reasons[pm.asa] += f", seen before in '{pm.form}'"
            out[opt.key] = _rank(scores, reasons, top_n, hits)
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_matcher.py`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add asa_mapper/matcher.py tests/test_matcher.py
git commit -m "feat: rank ASA suggestions by text, type and past mappings"
```

---

### Task 4: Past mappings, evaluation, SQLite store

**Files:**
- Create: `asa_mapper/past_mappings.py`, `asa_mapper/evaluate.py`, `asa_mapper/store.py`
- Test: `tests/test_past_mappings.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `Matcher` (Task 3), `parse_export` (Task 1), models.
- Produces: `existing_name_asa(field: FormField, library: Library) -> str`; `mappings_from_form(form: ParsedForm, library: Library) -> List[PastMapping]`; `decisions_to_mappings(form_name: str, review: dict, decisions: dict, library: Library) -> List[PastMapping]`; `evaluate(library: Library, past: List[PastMapping], top_n: int = 3) -> dict` with keys `forms, top_n, fields, options, misses`; `Store(path)` with `save_library(xlsx, source) -> str`, `load_library() -> Optional[tuple]`, `create_session(form_name, review, decisions=None) -> str`, `get_session(sid) -> Optional[dict]` (keys `id, form_name, created_at, finished_at, review, decisions`), `list_sessions() -> List[dict]`, `save_decisions(sid, decisions)`, `finish_session(sid)`, `replace_past(form, mappings)`, `list_past() -> List[PastMapping]`.
- Decision shape (used by Tasks 5–7): `{field_key: {"status": "pending"|"accepted"|"changed"|"none", "asa": str, "reviewer": str, "at": str, "options": {option_key: {"status": ..., "asa": str}}}}`.

- [ ] **Step 1: Write the failing tests**

```python file=tests/test_past_mappings.py
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
```

```python file=tests/test_store.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_past_mappings.py tests/test_store.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'asa_mapper.evaluate'`

- [ ] **Step 3: Implement past mappings**

```python file=asa_mapper/past_mappings.py
"""Turn existing ASAs in an export, or accepted review decisions, into PastMapping records."""
from __future__ import annotations

from typing import List

from .models import FormField, Library, ParsedForm, PastMapping

ACCEPTED = {"accepted", "changed"}


def existing_name_asa(field: FormField, library: Library) -> str:
    """The field's current Field name ASA: a direct link, else the parent of a linked option ASA."""
    for asa in field.existing_asa:
        entry = library.get(asa)
        if entry and entry.kind == "name":
            return asa
    for option in field.options:
        for asa in option.existing_asa:
            entry = library.get(asa)
            if entry and entry.kind == "option" and entry.parent:
                return entry.parent
    return ""


def _field_mapping(form: str, field: FormField, asa: str) -> PastMapping:
    return PastMapping(form=form, kind="field", label=field.label, path=field.path, headings=field.headings,
                       widget=field.widget, asa=asa, option_labels=[o.label for o in field.options])


def _option_mapping(form: str, field: FormField, label: str, asa: str, parent: str) -> PastMapping:
    return PastMapping(form=form, kind="option", label=label, path=field.path, headings=field.headings,
                       widget=field.widget, asa=asa, parent_asa=parent, field_label=field.label)


def mappings_from_form(form: ParsedForm, library: Library) -> List[PastMapping]:
    out = []
    for field in form.fields:
        if field.skip_reason:
            continue
        name_asa = existing_name_asa(field, library)
        if name_asa:
            out.append(_field_mapping(form.name, field, name_asa))
        for option in field.options:
            for asa in option.existing_asa:
                entry = library.get(asa)
                if entry and entry.kind == "option":
                    out.append(_option_mapping(form.name, field, option.label, asa, entry.parent))
                    break
    return out


def decisions_to_mappings(form_name: str, review: dict, decisions: dict, library: Library) -> List[PastMapping]:
    fields = {f["key"]: FormField.from_dict(f) for f in review["fields"]}
    out = []
    for key, decision in decisions.items():
        field = fields.get(key)
        if field is None or not isinstance(decision, dict):
            continue
        entry = library.get(decision.get("asa") or "")
        if decision.get("status") in ACCEPTED and entry and entry.kind == "name":
            out.append(_field_mapping(form_name, field, entry.asa))
        options = {o.key: o for o in field.options}
        for opt_key, opt_decision in (decision.get("options") or {}).items():
            option = options.get(opt_key)
            opt_entry = library.get(opt_decision.get("asa") or "")
            if option and opt_decision.get("status") in ACCEPTED and opt_entry and opt_entry.kind == "option":
                out.append(_option_mapping(form_name, field, option.label, opt_entry.asa, opt_entry.parent))
    return out
```

- [ ] **Step 4: Implement evaluation**

```python file=asa_mapper/evaluate.py
"""Leave-one-form-out accuracy of the matcher on known mappings."""
from __future__ import annotations

from typing import List

from .matcher import Matcher
from .models import FormField, FormOption, Library, PastMapping

MAX_MISSES = 200


def evaluate(library: Library, past: List[PastMapping], top_n: int = 3) -> dict:
    forms = sorted({p.form for p in past})
    stats = {"fields": {"total": 0, "top1": 0, "topn": 0}, "options": {"total": 0, "top1": 0, "topn": 0}}
    misses = []
    for form in forms:
        matcher = Matcher(library, [p for p in past if p.form != form])
        for pm in past:
            if pm.form != form or pm.asa not in library:
                continue
            if pm.kind == "field":
                query = FormField(key="", widget=pm.widget, label=pm.label, path=pm.path, headings=pm.headings,
                                  options=[FormOption(key=str(i), label=l) for i, l in enumerate(pm.option_labels)])
                ranked = [s.asa for s in matcher.suggest_field(query, top_n=top_n)]
                bucket = stats["fields"]
            else:
                query = FormField(key="", widget=pm.widget, label=pm.field_label, path=pm.path,
                                  headings=pm.headings, options=[FormOption(key="o", label=pm.label)])
                ranked = [s.asa for s in matcher.suggest_options(query, pm.parent_asa, top_n=top_n)["o"]]
                bucket = stats["options"]
            hit = bool(ranked) and ranked[0] == pm.asa
            bucket["total"] += 1
            bucket["top1"] += int(hit)
            bucket["topn"] += int(pm.asa in ranked)
            if not hit and len(misses) < MAX_MISSES:
                misses.append({"form": form, "kind": pm.kind, "label": pm.label, "path": " > ".join(pm.path),
                               "expected": pm.asa, "got": ranked[0] if ranked else ""})
    for bucket in stats.values():
        total = bucket["total"]
        bucket["top1_rate"] = round(bucket["top1"] / total, 3) if total else None
        bucket["topn_rate"] = round(bucket["topn"] / total, 3) if total else None
    return {"forms": len(forms), "top_n": top_n, **stats, "misses": misses}
```

- [ ] **Step 5: Implement the store**

```python file=asa_mapper/store.py
"""SQLite persistence: library snapshot, review sessions, past mappings."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .models import PastMapping

SCHEMA = """
CREATE TABLE IF NOT EXISTS library (
    id INTEGER PRIMARY KEY CHECK (id = 1), xlsx BLOB NOT NULL, source TEXT NOT NULL, synced_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, form_name TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
    review_json TEXT NOT NULL, decisions_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS past_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, form TEXT NOT NULL, data_json TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def save_library(self, xlsx: bytes, source: str) -> str:
        synced_at = now()
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO library (id, xlsx, source, synced_at) VALUES (1, ?, ?, ?)",
                         (xlsx, source, synced_at))
        return synced_at

    def load_library(self) -> Optional[Tuple[bytes, str, str]]:
        with self._connect() as conn:
            row = conn.execute("SELECT xlsx, source, synced_at FROM library WHERE id = 1").fetchone()
        return (bytes(row["xlsx"]), row["source"], row["synced_at"]) if row else None

    def create_session(self, form_name: str, review: dict, decisions: Optional[dict] = None) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, form_name, created_at, review_json, decisions_json) VALUES (?, ?, ?, ?, ?)",
                (sid, form_name, now(), json.dumps(review), json.dumps(decisions or {})))
        return sid

    def get_session(self, sid: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "form_name": row["form_name"], "created_at": row["created_at"],
                "finished_at": row["finished_at"], "review": json.loads(row["review_json"]),
                "decisions": json.loads(row["decisions_json"])}

    def list_sessions(self) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT id, form_name, created_at, finished_at FROM sessions "
                                "ORDER BY created_at DESC, rowid DESC").fetchall()
        return [dict(r) for r in rows]

    def save_decisions(self, sid: str, decisions: dict) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET decisions_json = ? WHERE id = ?", (json.dumps(decisions), sid))

    def finish_session(self, sid: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET finished_at = ? WHERE id = ?", (now(), sid))

    def replace_past(self, form: str, mappings: List[PastMapping]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM past_mappings WHERE form = ?", (form,))
            conn.executemany("INSERT INTO past_mappings (form, data_json) VALUES (?, ?)",
                             [(form, json.dumps(asdict(m))) for m in mappings])

    def list_past(self) -> List[PastMapping]:
        with self._connect() as conn:
            rows = conn.execute("SELECT data_json FROM past_mappings ORDER BY id").fetchall()
        return [PastMapping(**json.loads(r["data_json"])) for r in rows]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_past_mappings.py tests/test_store.py`
Expected: 7 passed

- [ ] **Step 7: Commit**

```bash
git add asa_mapper/past_mappings.py asa_mapper/evaluate.py asa_mapper/store.py tests/test_past_mappings.py tests/test_store.py
git commit -m "feat: past mappings, accuracy evaluation and SQLite store"
```

---

### Task 5: Review builder and review sheet

**Files:**
- Create: `asa_mapper/service.py`, `asa_mapper/exporter.py`
- Test: `tests/test_service_exporter.py`

**Interfaces:**
- Consumes: `Matcher`, `existing_name_asa`, models.
- Produces: `build_review(form: ParsedForm, matcher: Matcher, library: Library) -> dict` = `{"fields": [FormField dicts], "suggestions": {field_key: {"field": [Suggestion dicts], "parent": str, "options": {option_key: [Suggestion dicts]}, "flags": [str]}}}` (skipped fields have no entry); `option_suggestions(field: FormField, parent_asa: str, matcher: Matcher) -> Dict[str, List[dict]]`; `review_sheet(form_name: str, review: dict, decisions: dict) -> bytes`; `SHEET_HEADERS`.

- [ ] **Step 1: Write the failing tests**

```python file=tests/test_service_exporter.py
import io

import openpyxl

from asa_mapper.export_parser import parse_export
from asa_mapper.exporter import SHEET_HEADERS, review_sheet
from asa_mapper.matcher import Matcher
from asa_mapper.service import build_review


def make_review(export_dir, library):
    form = parse_export(export_dir)
    return form, build_review(form, Matcher(library), library)


def test_build_review(export_dir, library):
    _, review = make_review(export_dir, library)
    sug = review["suggestions"]
    assert "sig" not in sug and "split1" not in sug
    assert sug["phone1"]["field"][0]["asa"] == "asa_phone_primarycontact_contactinfo"
    assert sug["email1"]["field"][0]["asa"] == "asa_email_primarycontact_contactinfo"
    assert sug["bank"]["parent"] == "asa_bhc_specialentities"
    assert sug["bank"]["options"]["Yes"][0]["asa"] == "asa_yes_bhc_specialentities"
    assert sug["phone1"]["options"] == {}
    assert "not in library: asa_commitment_amount" in sug["commitment"]["flags"]
    assert len(review["fields"]) == 9


def test_review_sheet_rows(export_dir, library):
    form, review = make_review(export_dir, library)
    decisions = {"bank": {"status": "accepted", "asa": "asa_bhc_specialentities", "reviewer": "me@x",
                          "at": "2026-10-10T10:00:00+00:00",
                          "options": {"Yes": {"status": "accepted", "asa": "asa_yes_bhc_specialentities"}}}}
    ws = openpyxl.load_workbook(io.BytesIO(review_sheet(form.name, review, decisions))).active
    assert [c.value for c in ws[1]] == SHEET_HEADERS
    rows = [dict(zip(SHEET_HEADERS, [c.value or "" for c in r])) for r in ws.iter_rows(min_row=2)]
    bank = next(r for r in rows if r["Field key"] == "bank" and not r["Option key"])
    assert (bank["Accepted ASA"], bank["Status"], bank["Reviewer"]) == ("asa_bhc_specialentities", "accepted", "me@x")
    yes = next(r for r in rows if r["Field key"] == "bank" and r["Option key"] == "Yes")
    assert yes["Suggested ASA"] == yes["Accepted ASA"] == "asa_yes_bhc_specialentities"
    sig = next(r for r in rows if r["Field key"] == "sig")
    assert sig["Status"] == "skipped" and "Signature" in sig["Flags"]
    assert any(r["Field key"] == "commitment" and "not in library" in r["Flags"] for r in rows)
    assert next(r for r in rows if r["Field key"] == "phone1")["Status"] == "pending"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_service_exporter.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'asa_mapper.exporter'`

- [ ] **Step 3: Implement the review builder**

```python file=asa_mapper/service.py
"""Build a review: ranked suggestions and flags for every field of a parsed form."""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional

from .matcher import Matcher
from .models import FormField, Library, ParsedForm, Suggestion
from .past_mappings import existing_name_asa

OPTION_WIDGETS = {"Radio", "MultipleCheckbox"}


def option_suggestions(field: FormField, parent_asa: str, matcher: Matcher) -> Dict[str, List[dict]]:
    if field.widget not in OPTION_WIDGETS:
        return {}
    if not parent_asa:
        return {o.key: [] for o in field.options}
    return {key: [asdict(s) for s in ranked] for key, ranked in matcher.suggest_options(field, parent_asa).items()}


def field_flags(field: FormField, top: Optional[Suggestion], library: Library) -> List[str]:
    flags = [f"not in library: {a}" for a in field.existing_asa if a not in library]
    for option in field.options:
        flags += [f"option {option.key} not in library: {a}" for a in option.existing_asa if a not in library]
    existing = existing_name_asa(field, library)
    if existing and top and top.confidence == "High" and top.asa != existing:
        flags.append(f"possible mismatch: suggested {top.asa}")
    return flags


def build_review(form: ParsedForm, matcher: Matcher, library: Library) -> dict:
    suggestions = {}
    for field in form.fields:
        if field.skip_reason:
            continue
        ranked = matcher.suggest_field(field)
        parent = existing_name_asa(field, library) or (ranked[0].asa if ranked else "")
        suggestions[field.key] = {
            "field": [asdict(s) for s in ranked],
            "parent": parent,
            "options": option_suggestions(field, parent, matcher),
            "flags": field_flags(field, ranked[0] if ranked else None, library),
        }
    return {"fields": [f.to_dict() for f in form.fields], "suggestions": suggestions}
```

- [ ] **Step 4: Implement the review sheet**

```python file=asa_mapper/exporter.py
"""Write the review sheet (.xlsx): one row per field and per option."""
from __future__ import annotations

import io
from typing import List

import openpyxl
from openpyxl.styles import Font

SHEET_HEADERS = ["Page", "Section", "Label", "Field key", "Option key", "Widget", "Existing ASA",
                 "Suggested ASA", "Alternative 1", "Alternative 2", "Confidence", "Accepted ASA", "Status",
                 "Flags", "Reviewer", "Reviewed at"]
OPTION_WIDGETS = {"Radio", "MultipleCheckbox"}
WIDTHS = {"Label": 60, "Section": 40, "Suggested ASA": 45, "Alternative 1": 40, "Alternative 2": 40,
          "Accepted ASA": 45, "Existing ASA": 40, "Flags": 40}


def _top3(ranked: List[dict]) -> list:
    first = ranked[0] if ranked else {}
    alts = [r["asa"] for r in ranked[1:3]] + ["", ""]
    return [first.get("asa", ""), alts[0], alts[1], first.get("confidence", "")]


def review_sheet(form_name: str, review: dict, decisions: dict) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ASA review"
    ws.append(SHEET_HEADERS)
    for field in review["fields"]:
        path = field["path"]
        page, section = (path[0] if path else ""), " > ".join(path[1:])
        base = [page, section, field["label"], field["key"]]
        existing = ", ".join(field["existing_asa"])
        if field["skip_reason"]:
            ws.append(base + ["", field["widget"], existing, "", "", "", "", "", "skipped",
                              field["skip_reason"], "", ""])
            continue
        sug = review["suggestions"].get(field["key"], {})
        d = decisions.get(field["key"]) or {}
        ws.append(base + ["", field["widget"], existing] + _top3(sug.get("field", [])) +
                  [d.get("asa", ""), d.get("status", "pending"), "; ".join(sug.get("flags", [])),
                   d.get("reviewer", ""), d.get("at", "")])
        if field["widget"] not in OPTION_WIDGETS:
            continue
        for option in field["options"]:
            od = (d.get("options") or {}).get(option["key"]) or {}
            ws.append([page, section, option["label"], field["key"], option["key"], "option",
                       ", ".join(option["existing_asa"])] +
                      _top3(sug.get("options", {}).get(option["key"], [])) +
                      [od.get("asa", ""), od.get("status", "pending"), "", d.get("reviewer", ""), d.get("at", "")])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        ws.column_dimensions[cell.column_letter].width = WIDTHS.get(cell.value, 16)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_service_exporter.py`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add asa_mapper/service.py asa_mapper/exporter.py tests/test_service_exporter.py
git commit -m "feat: build review suggestions and export review sheet"
```

---

### Task 6: Settings, optional sign-in, API, CLI

**Files:**
- Create: `asa_mapper/settings.py`, `asa_mapper/auth.py`, `asa_mapper/api.py`, `asa_mapper/cli.py`, `asa_mapper/static/index.html` (placeholder replaced in Task 7)
- Test: `tests/test_api.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Settings` dataclass + `Settings.from_env()`; `install_auth(app, settings)`, `current_user(request) -> str`; `create_app(settings: Optional[Settings] = None) -> FastAPI`; `python -m asa_mapper.cli {library,seed,evaluate}`.
- HTTP API (used by Task 7): `GET /api/me`, `GET /api/library`, `POST /api/library/upload` (file), `POST /api/library/refresh`, `GET /api/search?q&kind&parent`, `GET /api/sessions`, `POST /api/sessions` (file), `GET /api/sessions/{id}`, `PUT /api/sessions/{id}/decisions`, `POST /api/sessions/{id}/options` `{field_key, parent_asa}`, `GET /api/sessions/{id}/sheet.xlsx`, `POST /api/sessions/{id}/finish`, `GET /api/accuracy`, `GET /healthz`.

- [ ] **Step 1: Write the failing tests**

```python file=tests/test_api.py
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
```

```python file=tests/test_cli.py
from asa_mapper.cli import main


def test_library_seed_evaluate(tmp_path, monkeypatch, capsys, library_xlsx, export_dir):
    monkeypatch.setenv("ASA_DATA_DIR", str(tmp_path / "data"))
    xlsx = tmp_path / "lib.xlsx"
    xlsx.write_bytes(library_xlsx)
    assert main(["evaluate"]) == 1
    assert main(["library", str(xlsx)]) == 0
    assert main(["seed", str(export_dir)]) == 0
    assert "Seeded 4 mappings from Sample Fund - Subscription Booklet (Version 3)" in capsys.readouterr().out
    assert main(["evaluate"]) == 0
    assert "Field names" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py tests/test_cli.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'asa_mapper.api'`

- [ ] **Step 3: Implement settings and sign-in**

```python file=asa_mapper/settings.py
"""Configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SHEET_ID = "1vXaCq5YjwfnUw_OsPilJ5WrXe5JHaFPZoKD4XejfhXk"


@dataclass
class Settings:
    data_dir: Path = Path("data")
    sheet_id: str = DEFAULT_SHEET_ID
    sheet_tab: str = "ASA List"
    service_account: str = ""  # JSON text or path to the service account key file
    google_client_id: str = ""  # sign-in is enabled when this is set
    google_client_secret: str = ""
    allowed_domain: str = "anduintransact.com"
    session_secret: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            data_dir=Path(env("ASA_DATA_DIR", "data")),
            sheet_id=env("ASA_SHEET_ID", DEFAULT_SHEET_ID),
            sheet_tab=env("ASA_SHEET_TAB", "ASA List"),
            service_account=env("ASA_GOOGLE_SERVICE_ACCOUNT", ""),
            google_client_id=env("ASA_GOOGLE_CLIENT_ID", ""),
            google_client_secret=env("ASA_GOOGLE_CLIENT_SECRET", ""),
            allowed_domain=env("ASA_ALLOWED_DOMAIN", "anduintransact.com"),
            session_secret=env("ASA_SESSION_SECRET", ""),
        )
```

```python file=asa_mapper/auth.py
"""Optional Google sign-in limited to one email domain. Off when no client id is configured."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from .settings import Settings

OPEN_PATHS = {"/login", "/auth/callback", "/logout", "/healthz"}


def current_user(request: Request) -> str:
    return (request.scope.get("session") or {}).get("user", {}).get("email", "")


def install_auth(app: FastAPI, settings: Settings) -> None:
    if not settings.google_client_id:
        return
    if not settings.google_client_secret or not settings.session_secret:
        raise ValueError("ASA_GOOGLE_CLIENT_SECRET and ASA_SESSION_SECRET are required when sign-in is enabled.")
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        "google", client_id=settings.google_client_id, client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"})
    domain = settings.allowed_domain.lower()

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if request.url.path in OPEN_PATHS or current_user(request):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        return RedirectResponse("/login")

    # Added after require_login so it runs first and the session is available there.
    app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")

    @app.get("/login")
    async def login(request: Request):
        return await oauth.google.authorize_redirect(request, str(request.url_for("auth_callback")), hd=domain)

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):
        token = await oauth.google.authorize_access_token(request)
        info = token.get("userinfo") or {}
        email = (info.get("email") or "").lower()
        if not info.get("email_verified") or not email.endswith("@" + domain):
            return HTMLResponse(f"Access is limited to @{domain} accounts.", status_code=403)
        request.session["user"] = {"email": email, "name": info.get("name", "")}
        return RedirectResponse("/")

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login")
```

- [ ] **Step 4: Implement the API**

```python file=asa_mapper/api.py
"""FastAPI app: upload exports, review suggestions, download the review sheet."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .asa_library import XLSX_MIME, LibraryError, fetch_google_sheet_xlsx, load_library_xlsx
from .auth import current_user, install_auth
from .evaluate import evaluate
from .export_parser import ExportError, form_label, parse_export
from .exporter import review_sheet
from .matcher import Matcher
from .models import FormField
from .past_mappings import decisions_to_mappings
from .service import build_review, option_suggestions
from .settings import Settings
from .store import Store

STATIC_DIR = Path(__file__).parent / "static"
SEARCH_LIMIT = 30


class OptionRequest(BaseModel):
    field_key: str
    parent_asa: str


class AppState:
    """Current library and matcher; rebuilt when the library or past mappings change."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = Store(settings.data_dir / "asa_mapper.sqlite")
        self.library = None
        self.matcher = None
        self.meta = {"source": None, "synced_at": None, "last_error": None}
        self.lock = threading.Lock()
        saved = self.store.load_library()
        if saved:
            data, source, synced_at = saved
            try:
                self.set_library(load_library_xlsx(data, settings.sheet_tab), source, synced_at)
            except LibraryError as exc:
                self.meta["last_error"] = f"Saved library unreadable: {exc}"
        elif settings.service_account:
            try:
                self.refresh_from_google()
            except Exception as exc:  # network/auth errors must not stop the app from starting
                self.meta["last_error"] = f"Google sync failed: {exc}"

    def set_library(self, library, source: str, synced_at: str) -> None:
        with self.lock:
            self.library = library
            self.meta.update(source=source, synced_at=synced_at)
            self.matcher = Matcher(library, self.store.list_past())

    def rebuild_matcher(self) -> None:
        with self.lock:
            if self.library:
                self.matcher = Matcher(self.library, self.store.list_past())

    def load_xlsx(self, data: bytes, source: str) -> None:
        library = load_library_xlsx(data, self.settings.sheet_tab)  # validate before saving anything
        synced_at = self.store.save_library(data, source)
        self.set_library(library, source, synced_at)
        self.meta["last_error"] = None

    def refresh_from_google(self) -> None:
        self.load_xlsx(fetch_google_sheet_xlsx(self.settings.sheet_id, self.settings.service_account), "Google Sheet")


def _used_asas(decisions: dict):
    for d in decisions.values():
        if isinstance(d, dict):
            yield d.get("asa")
            for od in (d.get("options") or {}).values():
                yield od.get("asa")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = AppState(settings)
    app = FastAPI(title="ASA Mapper")
    install_auth(app, settings)

    def require_library() -> None:
        if state.library is None:
            raise HTTPException(409, "Load the ASA Library first (upload the .xlsx or refresh from Google).")

    def require_session(sid: str) -> dict:
        session = state.store.get_session(sid)
        if session is None:
            raise HTTPException(404, "Review not found.")
        return session

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/me")
    def me(request: Request):
        return {"email": current_user(request)}

    @app.get("/api/library")
    def library_status():
        lib = state.library
        names = len(lib.names()) if lib else 0
        return {"loaded": lib is not None, **state.meta, "google_configured": bool(settings.service_account),
                "names": names, "options": len(lib.entries) - names if lib else 0,
                "issue_count": len(lib.issues) if lib else 0, "issues": lib.issues[:50] if lib else []}

    @app.post("/api/library/upload")
    async def upload_library(file: UploadFile = File(...)):
        try:
            state.load_xlsx(await file.read(), f"Uploaded {file.filename or 'file'}")
        except LibraryError as exc:
            raise HTTPException(400, str(exc))
        return library_status()

    @app.post("/api/library/refresh")
    def refresh_library():
        if not settings.service_account:
            raise HTTPException(503, "Google Sheet sync is not configured (set ASA_GOOGLE_SERVICE_ACCOUNT).")
        try:
            state.refresh_from_google()
        except Exception as exc:
            state.meta["last_error"] = f"Google sync failed: {exc}"
            raise HTTPException(502, f"{state.meta['last_error']}. Still using the last saved copy.")
        return library_status()

    @app.get("/api/search")
    def search(q: str = "", kind: str = "any", parent: str = ""):
        require_library()
        needle = q.lower().strip()
        out = []
        for e in state.library.entries:
            if (kind != "any" and e.kind != kind) or (parent and e.parent != parent):
                continue
            if needle and needle not in f"{e.asa} {e.summary} {e.section} {e.group}".lower():
                continue
            out.append({"asa": e.asa, "kind": e.kind, "summary": e.summary, "section": e.section,
                        "group": e.group, "parent": e.parent})
            if len(out) >= SEARCH_LIMIT:
                break
        return out

    @app.get("/api/sessions")
    def list_sessions():
        return state.store.list_sessions()

    @app.post("/api/sessions")
    async def create_session(file: UploadFile = File(...)):
        require_library()
        filename = file.filename or "form.zip"
        try:
            form = parse_export(await file.read(), fallback_name=Path(filename).stem)
        except ExportError as exc:
            raise HTTPException(400, str(exc))
        form.name = form_label(form.name, filename)
        review = build_review(form, state.matcher, state.library)
        sid = state.store.create_session(form.name, review)
        fields = review["fields"]
        return {"id": sid, "form_name": form.name, "fields": len(fields),
                "skipped": sum(1 for f in fields if f["skip_reason"]),
                "existing_asa": sum(1 for f in fields if f["existing_asa"])}

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str):
        session = require_session(sid)
        if state.library:
            session["missing_asas"] = sorted({a for a in _used_asas(session["decisions"])
                                              if a and a not in state.library})
        return session

    @app.put("/api/sessions/{sid}/decisions")
    def save_decisions(sid: str, request: Request, decisions: dict = Body(...)):
        previous = require_session(sid)["decisions"]
        user = current_user(request)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for key, d in decisions.items():
            if not isinstance(d, dict) or d.get("status", "pending") == "pending":
                continue
            p = previous.get(key) or {}
            unchanged = (p.get("status"), p.get("asa"), p.get("options")) == (d.get("status"), d.get("asa"), d.get("options"))
            d["reviewer"] = p.get("reviewer", user) if unchanged else user
            d["at"] = p.get("at", stamp) if unchanged else stamp
        state.store.save_decisions(sid, decisions)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/options")
    def suggest_options(sid: str, req: OptionRequest):
        require_library()
        session = require_session(sid)
        match = next((f for f in session["review"]["fields"] if f["key"] == req.field_key), None)
        if match is None:
            raise HTTPException(404, "Field not found.")
        return option_suggestions(FormField.from_dict(match), req.parent_asa, state.matcher)

    @app.get("/api/sessions/{sid}/sheet.xlsx")
    def download_sheet(sid: str):
        session = require_session(sid)
        data = review_sheet(session["form_name"], session["review"], session["decisions"])
        filename = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", session["form_name"]) + " - ASA review.xlsx"
        return Response(data, media_type=XLSX_MIME,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/sessions/{sid}/finish")
    def finish(sid: str):
        require_library()
        session = require_session(sid)
        mappings = decisions_to_mappings(session["form_name"], session["review"], session["decisions"], state.library)
        state.store.replace_past(session["form_name"], mappings)
        state.store.finish_session(sid)
        state.rebuild_matcher()
        return {"saved": len(mappings)}

    @app.get("/api/accuracy")
    def accuracy():
        require_library()
        return evaluate(state.library, state.store.list_past())

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
```

- [ ] **Step 5: Implement the CLI and a placeholder page**

```python file=asa_mapper/cli.py
"""Command line: import the library, seed past mappings from mapped exports, print accuracy."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .asa_library import load_library_xlsx
from .evaluate import evaluate
from .export_parser import form_label, parse_export
from .past_mappings import mappings_from_form
from .settings import Settings
from .store import Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asa_mapper.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lib = sub.add_parser("library", help="Import the ASA Library from an .xlsx file")
    lib.add_argument("xlsx", type=Path)
    seed = sub.add_parser("seed", help="Store existing ASA mappings from mapped form exports")
    seed.add_argument("exports", nargs="+", type=Path)
    sub.add_parser("evaluate", help="Print accuracy on stored mappings")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = Store(settings.data_dir / "asa_mapper.sqlite")
    if args.cmd == "library":
        data = args.xlsx.read_bytes()
        library = load_library_xlsx(data, settings.sheet_tab)
        store.save_library(data, f"Uploaded {args.xlsx.name}")
        names = len(library.names())
        print(f"Loaded {names} field names, {len(library.entries) - names} options, {len(library.issues)} issues")
        return 0

    saved = store.load_library()
    if not saved:
        print("Load the library first: python -m asa_mapper.cli library <file.xlsx>", file=sys.stderr)
        return 1
    library = load_library_xlsx(saved[0], settings.sheet_tab)

    if args.cmd == "seed":
        for path in args.exports:
            form = parse_export(path)
            form.name = form_label(form.name, path.name)
            mappings = mappings_from_form(form, library)
            store.replace_past(form.name, mappings)
            print(f"Seeded {len(mappings)} mappings from {form.name}")
        return 0

    report = evaluate(library, store.list_past())
    pct = lambda x: "-" if x is None else f"{x:.0%}"  # noqa: E731
    print(f"Forms: {report['forms']}")
    for label, key in (("Field names", "fields"), ("Field options", "options")):
        b = report[key]
        print(f"{label}: {b['total']} tested, first choice {pct(b['top1_rate'])}, "
              f"top {report['top_n']} {pct(b['topn_rate'])}")
    for miss in report["misses"][:20]:
        print(f"  MISS [{miss['kind']}] {miss['label'][:60]!r}: expected {miss['expected']}, got {miss['got'] or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```html file=asa_mapper/static/index.html
<!doctype html>
<title>ASA Mapper</title>
<p>ASA Mapper</p>
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest`
Expected: all tests pass (36 passed)

- [ ] **Step 7: Commit**

```bash
git add asa_mapper tests
git commit -m "feat: API, optional Google sign-in and CLI"
```

---

### Task 7: Review page (HTML/JS/CSS)

**Files:**
- Modify: `asa_mapper/static/index.html` (replace placeholder)
- Create: `asa_mapper/static/app.js`, `asa_mapper/static/style.css`
- Test: `tests/test_api.py::test_index_served` (already covers serving); manual browser check in Task 8.

**Interfaces:**
- Consumes: HTTP API from Task 6; decision shape from Task 4.

- [ ] **Step 1: Write the page**

```html file=asa_mapper/static/index.html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ASA Mapper</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header class="top">
  <h1><a href="#/">ASA Mapper</a></h1>
  <div id="library-status" class="library">Loading library…</div>
  <nav class="library-actions">
    <button id="refresh-library" type="button" hidden>Refresh from Google</button>
    <label class="button">Upload library .xlsx<input id="library-file" type="file" accept=".xlsx" hidden></label>
    <a href="#/accuracy">Accuracy</a>
    <span id="user" class="muted"></span>
  </nav>
</header>
<main>
  <section id="view-home" hidden>
    <h2>Start a review</h2>
    <label class="drop" id="drop">Drop a form export .zip here, or click to choose
      <input id="export-file" type="file" accept=".zip" hidden></label>
    <p id="upload-msg" class="msg"></p>
    <h2>Reviews</h2>
    <table class="list">
      <thead><tr><th>Form</th><th>Started</th><th>Finished</th></tr></thead>
      <tbody id="sessions"></tbody>
    </table>
  </section>

  <section id="view-review" hidden>
    <div class="review-head">
      <h2 id="form-name"></h2>
      <div class="progress"><div id="progress-bar"></div></div>
      <span id="progress-text" class="muted"></span>
    </div>
    <div class="toolbar">
      <select id="filter-status" aria-label="Status filter">
        <option value="all">All fields</option>
        <option value="needs">Needs review</option>
        <option value="pending">Not reviewed</option>
        <option value="done">Reviewed</option>
        <option value="skipped">Skipped</option>
      </select>
      <select id="filter-section" aria-label="Section filter"></select>
      <input id="filter-text" type="search" placeholder="Search label or key">
      <span class="spacer"></span>
      <a id="download" class="button" href="#">Download review sheet</a>
      <button id="finish" type="button" class="primary">Finish review</button>
    </div>
    <p id="review-msg" class="msg"></p>
    <div id="review-body"></div>
  </section>

  <section id="view-accuracy" hidden>
    <h2>Accuracy on known mappings</h2>
    <p class="muted">Runs the matcher on stored mappings (seeded forms and finished reviews). Each form is scored without its own mappings.</p>
    <button id="run-accuracy" type="button" class="primary">Run</button>
    <div id="accuracy-body"></div>
  </section>
</main>

<dialog id="search-dialog">
  <form method="dialog">
    <h3>Choose an ASA</h3>
    <input id="search-input" type="search" placeholder="Search alias, meaning or section">
    <ul id="search-results"></ul>
    <menu><button value="cancel">Cancel</button></menu>
  </form>
</dialog>
<script src="/static/app.js"></script>
</body>
</html>
```

```javascript file=asa_mapper/static/app.js
"use strict";

const $ = (sel) => document.querySelector(sel);
const OPTION_WIDGETS = new Set(["Radio", "MultipleCheckbox"]);
const STATUS_TEXT = { pending: "Not reviewed", accepted: "Accepted", changed: "Changed", none: "No ASA" };
const state = { session: null, decisions: {}, saveTimer: null };

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "");

async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 401) { location.href = "/login"; throw new Error("Not signed in"); }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const body = isJson ? await res.json() : null;
  if (!res.ok) throw new Error((body && body.detail) || res.statusText);
  return body;
}
const jsonOpts = (method, data) => ({ method, headers: { "content-type": "application/json" }, body: JSON.stringify(data) });

/* ---------- library ---------- */
async function loadLibraryStatus() {
  const s = await api("/api/library");
  const el = $("#library-status");
  if (!s.loaded) {
    el.innerHTML = `<span class="warn">No ASA Library loaded.</span> Upload the .xlsx or refresh from Google.`;
  } else {
    const issues = s.issue_count ? ` · <span class="muted" title="${esc(s.issues.join("\n"))}">${s.issue_count} library issues</span>` : "";
    el.innerHTML = `ASA List · ${s.names} field names, ${s.options} options · ${esc(s.source)} · ${when(s.synced_at)}${issues}`;
  }
  if (s.last_error) el.innerHTML += ` <span class="warn">${esc(s.last_error)}</span>`;
  $("#refresh-library").hidden = !s.google_configured;
}

$("#refresh-library").onclick = async () => {
  try { await api("/api/library/refresh", { method: "POST" }); } catch (e) { alert(e.message); }
  loadLibraryStatus();
};
$("#library-file").onchange = async (ev) => {
  const fd = new FormData();
  fd.append("file", ev.target.files[0]);
  try { await api("/api/library/upload", { method: "POST", body: fd }); } catch (e) { alert(e.message); }
  ev.target.value = "";
  loadLibraryStatus();
};

/* ---------- home ---------- */
async function showHome() {
  const rows = await api("/api/sessions");
  $("#sessions").innerHTML = rows.map((r) =>
    `<tr><td><a href="#/review/${r.id}">${esc(r.form_name)}</a></td><td>${when(r.created_at)}</td><td>${when(r.finished_at)}</td></tr>`
  ).join("") || `<tr><td colspan="3" class="muted">No reviews yet.</td></tr>`;
}

async function uploadExport(file) {
  const msg = $("#upload-msg");
  msg.textContent = "Reading the export and preparing suggestions…";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await api("/api/sessions", { method: "POST", body: fd });
    msg.textContent = "";
    location.hash = `#/review/${r.id}`;
  } catch (e) { msg.textContent = e.message; }
}
$("#export-file").onchange = (ev) => { if (ev.target.files[0]) uploadExport(ev.target.files[0]); ev.target.value = ""; };
const drop = $("#drop");
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => {
  e.preventDefault();
  drop.classList.remove("over");
  if (e.dataTransfer.files[0]) uploadExport(e.dataTransfer.files[0]);
};

/* ---------- review: helpers ---------- */
const sectionOf = (f) => f.path.slice(1).join(" › ") || f.path[0] || "(no section)";
const fieldByKey = (key) => state.session.review.fields.find((f) => f.key === key);
const suggOf = (key) => state.session.review.suggestions[key];
const decided = (d) => !!d && !!d.status && d.status !== "pending";
const hasOptions = (f) => OPTION_WIDGETS.has(f.widget) && f.options.length > 0;

function ensure(key) {
  if (!state.decisions[key]) state.decisions[key] = { status: "pending", asa: "", options: {} };
  if (!state.decisions[key].options) state.decisions[key].options = {};
  return state.decisions[key];
}

function isDone(f) {
  const d = state.decisions[f.key];
  if (!decided(d)) return false;
  return !hasOptions(f) || f.options.every((o) => decided((d.options || {})[o.key]));
}

function needsReview(f) {
  if (isDone(f)) return false;
  const sug = suggOf(f.key);
  if (sug.flags.length || (sug.field[0] || {}).confidence !== "High") return true;
  return hasOptions(f) && f.options.some((o) => ((sug.options[o.key] || [])[0] || {}).confidence !== "High");
}

/* ---------- review: rendering ---------- */
function asaSelect(ranked, existing, current) {
  const seen = new Set();
  const opts = [];
  const add = (asa, text) => {
    if (!asa || seen.has(asa)) return;
    seen.add(asa);
    opts.push(`<option value="${esc(asa)}"${asa === current ? " selected" : ""}>${esc(text)}</option>`);
  };
  ranked.forEach((r, i) => add(r.asa, `${i === 0 ? "★ " : ""}${r.asa}`));
  existing.forEach((a) => add(a, `${a} (existing)`));
  add(current, current);
  return `<select data-asa>${opts.join("")}<option value="__none"${current ? "" : " selected"}>— No ASA —</option>` +
    `<option value="__search">Search library…</option></select>`;
}

function confBadge(s) {
  return s ? `<span class="conf conf-${s.confidence.toLowerCase()}" title="${esc(s.reason)}">${s.confidence}</span>` : "";
}
const statusBadge = (status) => `<span class="status status-${status || "pending"}">${STATUS_TEXT[status || "pending"]}</span>`;

function existingCell(asas, flags) {
  const list = asas.map((a) => `<div class="mono">${esc(a)}</div>`).join("");
  return list + flags.map((f) => `<div class="flag">⚠ ${esc(f)}</div>`).join("");
}

function labelCell(f) {
  const meta = [f.key, f.widget, f.hidden ? "hidden" : "", f.headings.join(" › ")].filter(Boolean).join(" · ");
  return `<div class="label">${esc(f.label)}</div><div class="meta">${esc(meta)}</div>`;
}

function currentAsa(d, ranked) {
  return decided(d) ? d.asa : ((ranked[0] || {}).asa || "");
}

function fieldRows(f) {
  if (f.skip_reason) {
    return `<tr class="skipped"><td>${labelCell(f)}</td><td>${existingCell(f.existing_asa, [])}</td>` +
      `<td colspan="4" class="muted">Skipped: ${esc(f.skip_reason)}</td></tr>`;
  }
  const sug = suggOf(f.key);
  const d = state.decisions[f.key];
  const current = currentAsa(d, sug.field);
  let html = `<tr class="field" data-key="${esc(f.key)}">
    <td>${labelCell(f)}</td>
    <td>${existingCell(f.existing_asa, sug.flags)}</td>
    <td>${asaSelect(sug.field, f.existing_asa, current)}</td>
    <td>${confBadge(sug.field.find((r) => r.asa === current))}</td>
    <td>${statusBadge(d && d.status)}</td>
    <td><button type="button" data-accept>Accept</button></td></tr>`;
  if (!hasOptions(f)) return html;
  for (const o of f.options) {
    const od = ((d && d.options) || {})[o.key];
    const ranked = sug.options[o.key] || [];
    const cur = currentAsa(od, ranked);
    html += `<tr class="option" data-key="${esc(f.key)}" data-opt="${esc(o.key)}">
      <td class="opt-label">↳ ${esc(o.label)}</td>
      <td>${existingCell(o.existing_asa, [])}</td>
      <td>${asaSelect(ranked, o.existing_asa, cur)}</td>
      <td>${confBadge(ranked.find((r) => r.asa === cur))}</td>
      <td>${statusBadge(od && od.status)}</td>
      <td><button type="button" data-accept>Accept</button></td></tr>`;
  }
  return html;
}

function renderReview() {
  const s = state.session;
  const status = $("#filter-status").value;
  const section = $("#filter-section").value;
  const text = $("#filter-text").value.toLowerCase();
  const groups = new Map();
  for (const f of s.review.fields) {
    const skipped = !!f.skip_reason;
    if (status === "skipped" ? !skipped : skipped) continue;
    if (section && sectionOf(f) !== section) continue;
    if (text && !f.label.toLowerCase().includes(text) && !f.key.toLowerCase().includes(text)) continue;
    if (status === "pending" && isDone(f)) continue;
    if (status === "done" && !isDone(f)) continue;
    if (status === "needs" && !needsReview(f)) continue;
    const key = sectionOf(f);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  }
  let html = "";
  for (const [name, fields] of groups) {
    html += `<div class="section"><div class="section-head"><h3>${esc(name)}</h3>` +
      (status === "skipped" ? "" : `<button type="button" data-accept-high="${esc(name)}">Accept all High</button>`) +
      `</div><table class="fields"><thead><tr><th>Field</th><th>Existing ASA</th><th>ASA</th><th>Confidence</th><th>Status</th><th></th></tr></thead><tbody>` +
      fields.map(fieldRows).join("") + `</tbody></table></div>`;
  }
  $("#review-body").innerHTML = html || `<p class="muted">No fields match the filters.</p>`;
  updateProgress();
}

function updateProgress() {
  let total = 0;
  let done = 0;
  for (const f of state.session.review.fields) {
    if (f.skip_reason) continue;
    const d = state.decisions[f.key];
    total += 1;
    done += decided(d) ? 1 : 0;
    if (!hasOptions(f)) continue;
    for (const o of f.options) {
      total += 1;
      done += decided(((d && d.options) || {})[o.key]) ? 1 : 0;
    }
  }
  $("#progress-bar").style.width = total ? `${(100 * done) / total}%` : "0";
  $("#progress-text").textContent = `${done} of ${total} reviewed`;
}

/* ---------- review: changes ---------- */
async function setDecision(key, opt, asa, forcedStatus) {
  const sug = suggOf(key);
  const d = ensure(key);
  if (opt === undefined) {
    const top = (sug.field[0] || {}).asa || "";
    d.asa = asa;
    d.status = forcedStatus || (asa === top ? "accepted" : "changed");
    if (hasOptions(fieldByKey(key)) && asa && asa !== sug.parent) {
      sug.options = await api(`/api/sessions/${state.session.id}/options`, jsonOpts("POST", { field_key: key, parent_asa: asa }));
      sug.parent = asa;
      d.options = {};
    }
  } else {
    const top = ((sug.options[opt] || [])[0] || {}).asa || "";
    d.options[opt] = { asa, status: forcedStatus || (asa === top ? "accepted" : "changed") };
  }
  scheduleSave();
  renderReview();
}

function acceptHigh(section) {
  for (const f of state.session.review.fields) {
    if (f.skip_reason || sectionOf(f) !== section) continue;
    const sug = suggOf(f.key);
    const d = ensure(f.key);
    const top = sug.field[0];
    if (!decided(d) && top && top.confidence === "High") Object.assign(d, { asa: top.asa, status: "accepted" });
    if (!hasOptions(f)) continue;
    for (const o of f.options) {
      const ot = (sug.options[o.key] || [])[0];
      if (!decided(d.options[o.key]) && ot && ot.confidence === "High") d.options[o.key] = { asa: ot.asa, status: "accepted" };
    }
  }
  scheduleSave();
  renderReview();
}

function scheduleSave() {
  clearTimeout(state.saveTimer);
  $("#review-msg").textContent = "Saving…";
  state.saveTimer = setTimeout(save, 400);
}

async function save() {
  try {
    await api(`/api/sessions/${state.session.id}/decisions`, jsonOpts("PUT", state.decisions));
    $("#review-msg").textContent = "All changes saved.";
  } catch (e) { $("#review-msg").textContent = `Save failed: ${e.message}`; }
}

function openSearch(kind, parent) {
  const dlg = $("#search-dialog");
  const input = $("#search-input");
  const results = $("#search-results");
  input.value = "";
  return new Promise((resolve) => {
    let chosen = null;
    const run = async () => {
      const rows = await api(`/api/search?${new URLSearchParams({ q: input.value, kind, parent })}`);
      results.innerHTML = rows.map((r) =>
        `<li><button type="button" data-pick="${esc(r.asa)}"><strong class="mono">${esc(r.asa)}</strong>` +
        `<span>${esc(r.summary)} · ${esc(r.section)}${r.group ? ` · ${esc(r.group)}` : ""}</span></button></li>`
      ).join("") || `<li class="muted">No matches</li>`;
    };
    input.oninput = run;
    results.onclick = (e) => {
      const b = e.target.closest("[data-pick]");
      if (b) { chosen = b.dataset.pick; dlg.close(); }
    };
    dlg.onclose = () => resolve(chosen);
    dlg.showModal();
    input.focus();
    run();
  });
}

$("#review-body").addEventListener("change", async (ev) => {
  const sel = ev.target.closest("select[data-asa]");
  if (!sel) return;
  const row = sel.closest("tr");
  const { key, opt } = row.dataset;
  let value = sel.value;
  if (value === "__search") {
    value = await openSearch(opt === undefined ? "name" : "option", opt === undefined ? "" : suggOf(key).parent);
    if (value === null) { renderReview(); return; }
  }
  if (value === "__none") await setDecision(key, opt, "", "none");
  else await setDecision(key, opt, value, null);
});

$("#review-body").addEventListener("click", async (ev) => {
  const high = ev.target.closest("[data-accept-high]");
  if (high) { acceptHigh(high.dataset.acceptHigh); return; }
  const btn = ev.target.closest("[data-accept]");
  if (!btn) return;
  const row = btn.closest("tr");
  const value = row.querySelector("select[data-asa]").value;
  if (value === "__search") return;
  if (value === "__none") await setDecision(row.dataset.key, row.dataset.opt, "", "none");
  else await setDecision(row.dataset.key, row.dataset.opt, value, null);
});

for (const id of ["#filter-status", "#filter-section"]) $(id).onchange = renderReview;
$("#filter-text").oninput = renderReview;

$("#finish").onclick = async () => {
  if (!confirm("Finish this review and save the accepted ASAs to improve future suggestions?")) return;
  clearTimeout(state.saveTimer);
  await save();
  try {
    const r = await api(`/api/sessions/${state.session.id}/finish`, { method: "POST" });
    $("#review-msg").textContent = `Review finished. Saved ${r.saved} mappings for future suggestions.`;
  } catch (e) { $("#review-msg").textContent = e.message; }
};

async function showReview(id) {
  const s = await api(`/api/sessions/${id}`);
  state.session = s;
  state.decisions = s.decisions || {};
  $("#form-name").textContent = s.form_name;
  $("#download").href = `/api/sessions/${id}/sheet.xlsx`;
  const sections = [...new Set(s.review.fields.filter((f) => !f.skip_reason).map(sectionOf))];
  $("#filter-section").innerHTML = `<option value="">All sections</option>` +
    sections.map((x) => `<option>${esc(x)}</option>`).join("");
  $("#review-msg").innerHTML = (s.missing_asas || []).length
    ? `<span class="warn">Accepted ASAs no longer in the library: ${esc(s.missing_asas.join(", "))}</span>` : "";
  renderReview();
}

/* ---------- accuracy ---------- */
$("#run-accuracy").onclick = async () => {
  const body = $("#accuracy-body");
  body.textContent = "Running…";
  try {
    const r = await api("/api/accuracy");
    const pct = (x) => (x === null ? "–" : `${Math.round(x * 100)}%`);
    const row = (name, b) => `<tr><td>${name}</td><td>${b.total}</td><td>${pct(b.top1_rate)}</td><td>${pct(b.topn_rate)}</td></tr>`;
    body.innerHTML = `<p>${r.forms} form(s) with known mappings.</p>
      <table class="list"><thead><tr><th></th><th>Tested</th><th>First suggestion correct</th><th>In top ${r.top_n}</th></tr></thead>
      <tbody>${row("Field names", r.fields)}${row("Field options", r.options)}</tbody></table>
      <h3>Misses (${r.misses.length})</h3>
      <table class="list"><thead><tr><th>Kind</th><th>Label</th><th>Section</th><th>Expected</th><th>Got</th></tr></thead>
      <tbody>${r.misses.map((m) => `<tr><td>${m.kind}</td><td>${esc(m.label)}</td><td>${esc(m.path)}</td>` +
        `<td class="mono">${esc(m.expected)}</td><td class="mono">${esc(m.got)}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { body.textContent = e.message; }
};

/* ---------- routing ---------- */
async function route() {
  const hash = location.hash || "#/";
  for (const v of ["home", "review", "accuracy"]) $(`#view-${v}`).hidden = true;
  try {
    if (hash.startsWith("#/review/")) {
      $("#view-review").hidden = false;
      await showReview(hash.slice("#/review/".length));
    } else if (hash === "#/accuracy") {
      $("#view-accuracy").hidden = false;
    } else {
      $("#view-home").hidden = false;
      await showHome();
    }
  } catch (e) { alert(e.message); }
}

window.onhashchange = route;
loadLibraryStatus();
api("/api/me").then((u) => { $("#user").textContent = u.email || ""; }).catch(() => {});
route();
```

```css file=asa_mapper/static/style.css
:root {
  --bg: #f7f8fa; --panel: #ffffff; --text: #1d2330; --muted: #667085; --border: #dde1e7;
  --accent: #2f6fde; --accent-text: #ffffff; --warn: #b42318; --warn-bg: #fef3f2;
  --high: #067647; --high-bg: #ecfdf3; --medium: #b54708; --medium-bg: #fffaeb; --low: #b42318; --low-bg: #fef3f2;
  --row-done: #f6fef9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #12151b; --panel: #1b2029; --text: #e6e9ef; --muted: #98a2b3; --border: #2e3542;
    --accent: #5b8def; --warn: #f97066; --warn-bg: #3a1d1b;
    --high: #47cd89; --high-bg: #133024; --medium: #fdb022; --medium-bg: #3a2a10; --low: #f97066; --low-bg: #3a1d1b;
    --row-done: #15241c;
  }
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
a { color: var(--accent); }
.top { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 24px; padding: 12px 24px; background: var(--panel); border-bottom: 1px solid var(--border); }
.top h1 { font-size: 18px; margin: 0; }
.top h1 a { color: var(--text); text-decoration: none; }
.library { flex: 1; color: var(--muted); }
.library-actions { display: flex; gap: 12px; align-items: center; }
main { padding: 16px 24px 48px; max-width: 1500px; margin: 0 auto; }
button, .button, select, input[type=search] { font: inherit; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--panel); color: var(--text); cursor: pointer; text-decoration: none; display: inline-block; }
input[type=search] { cursor: text; min-width: 220px; }
.primary { background: var(--accent); color: var(--accent-text); border-color: var(--accent); }
.drop { display: block; padding: 40px; text-align: center; border: 2px dashed var(--border); border-radius: 10px; background: var(--panel); cursor: pointer; color: var(--muted); }
.drop.over { border-color: var(--accent); color: var(--accent); }
.msg { min-height: 1.4em; color: var(--muted); }
.muted { color: var(--muted); }
.warn, .flag { color: var(--warn); }
.flag { font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; word-break: break-all; }
table { width: 100%; border-collapse: collapse; background: var(--panel); }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 12px; }
.review-head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.review-head h2 { margin: 8px 0; flex: 1; }
.progress { width: 220px; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }
#progress-bar { height: 100%; width: 0; background: var(--accent); }
.toolbar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 8px 0; position: sticky; top: 0; background: var(--bg); padding: 8px 0; z-index: 1; }
.spacer { flex: 1; }
.section { margin: 20px 0; }
.section-head { display: flex; align-items: center; gap: 12px; }
.section-head h3 { margin: 8px 0; font-size: 15px; flex: 1; }
.fields td:nth-child(1) { width: 34%; }
.fields td:nth-child(2) { width: 20%; }
.fields td:nth-child(3) select { width: 100%; max-width: 420px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.label { font-weight: 500; }
.meta { color: var(--muted); font-size: 12px; }
tr.option td { background: var(--bg); }
.opt-label { padding-left: 24px; }
tr.skipped td { color: var(--muted); }
.conf, .status { font-size: 12px; padding: 2px 8px; border-radius: 10px; white-space: nowrap; }
.conf-high { color: var(--high); background: var(--high-bg); }
.conf-medium { color: var(--medium); background: var(--medium-bg); }
.conf-low { color: var(--low); background: var(--low-bg); }
.status-accepted, .status-changed { color: var(--high); background: var(--high-bg); }
.status-none { color: var(--muted); background: var(--bg); }
.status-pending { color: var(--muted); }
dialog { width: min(720px, 92vw); border: 1px solid var(--border); border-radius: 10px; background: var(--panel); color: var(--text); }
dialog input { width: 100%; }
#search-results { list-style: none; padding: 0; max-height: 50vh; overflow: auto; }
#search-results button { width: 100%; text-align: left; display: flex; flex-direction: column; gap: 2px; margin: 4px 0; }
#search-results span { color: var(--muted); font-size: 12px; }
menu { display: flex; justify-content: flex-end; padding: 0; }
```

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/pytest`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add asa_mapper/static
git commit -m "feat: review page for ASA suggestions"
```

---

### Task 8: Real-data check, Docker files, README

**Files:**
- Create: `README.md`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `.dockerignore`
- Test: manual run against private data (not committed)

- [ ] **Step 1: Load the real library and seed the mapped form**

```bash
mkdir -p fixtures/private
cp "/private/tmp/claude-502/-Users-rozacsaid/05081c3e-b95d-4b3c-99fb-946c79aa68eb/scratchpad/asa_library.xlsx" fixtures/private/asa_library.xlsx
.venv/bin/python -m asa_mapper.cli library fixtures/private/asa_library.xlsx
.venv/bin/python -m asa_mapper.cli seed "/Users/rozacsaid/Downloads/8VC Fund VI_ L.P. - Subscription Booklet - Version 36"
.venv/bin/python -m asa_mapper.cli evaluate
```
Expected: library loads ~513 field names / ~729 options; seed reports ~140 mappings; evaluate prints first-choice rates. Compare with the baseline (fields 63%, options 66%). If below, tune weights in `matcher.py` (`TYPE_MISMATCH_FACTOR`, query composition) and re-run; record final numbers in the README.

- [ ] **Step 2: Start the app and check the flow in a browser**

Run: `.venv/bin/uvicorn asa_mapper.api:create_app --factory --port 8000`
Then: open http://localhost:8000, upload `~/Downloads/8VC Fund VI_ L.P. - Subscription Booklet - Version 7.zip`, check suggestions appear, accept a few, change one through "Search library…", download the review sheet and open it.

- [ ] **Step 3: Write deployment files**

```dockerfile file=Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY asa_mapper ./asa_mapper
ENV ASA_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "asa_mapper.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
```

```yaml file=docker-compose.yml
services:
  asa-mapper:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    volumes:
      - ./data:/data
```

```text file=.env.example
# Copy to .env and fill in. All values are optional for local use.
ASA_DATA_DIR=data
ASA_SHEET_ID=1vXaCq5YjwfnUw_OsPilJ5WrXe5JHaFPZoKD4XejfhXk
ASA_SHEET_TAB=ASA List
# Service account JSON (text or file path); share the sheet with its email to enable "Refresh from Google".
ASA_GOOGLE_SERVICE_ACCOUNT=
# Google sign-in (leave ASA_GOOGLE_CLIENT_ID empty to disable).
ASA_GOOGLE_CLIENT_ID=
ASA_GOOGLE_CLIENT_SECRET=
ASA_ALLOWED_DOMAIN=anduintransact.com
ASA_SESSION_SECRET=
```

```text file=.dockerignore
.venv
data
fixtures
tests
.git
```

```markdown file=README.md
# ASA Mapper

Suggests Anduin Standard Aliases (ASAs) for the fields and options of an Anduin form export, lets a DS review them, and downloads a review sheet.

Design: `docs/superpowers/specs/2026-10-10-asa-mapper-design.md`

## Run locally

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/uvicorn asa_mapper.api:create_app --factory --port 8000

Open http://localhost:8000. Load the ASA Library with **Upload library .xlsx** (in Google Sheets: File → Download → Microsoft Excel), then drop a form export `.zip`.

## Command line

    .venv/bin/python -m asa_mapper.cli library path/to/ASA\ Library.xlsx   # import the library
    .venv/bin/python -m asa_mapper.cli seed path/to/mapped-export ...       # store known mappings
    .venv/bin/python -m asa_mapper.cli evaluate                             # accuracy on known mappings

## How suggestions work (version 1, no AI)

- Text fields, Radio groups and Checkbox groups are matched to **Field name** ASAs; Radio/Checkbox options to **Field option** ASAs under the field's ASA.
- Score = TF-IDF text similarity (label, nearby headings, section path, option labels vs. the ASA's section, field group, meaning, keywords), halved when the ASA's input type doesn't fit the widget, plus a boost when the same label was mapped before.
- Finished reviews are saved as past mappings and improve later suggestions.

## Configuration

See `.env.example`. Google Sheet sync needs a service account the sheet is shared with. Google sign-in is off unless `ASA_GOOGLE_CLIENT_ID` is set.

## Data

Client exports and the library copy live in `fixtures/private/` and `data/`, both git-ignored. Uploaded exports are parsed in memory and not stored.

## Tests

    .venv/bin/pytest
```

- [ ] **Step 4: Commit**

```bash
git add README.md Dockerfile docker-compose.yml .env.example .dockerignore
git commit -m "docs: README and container files"
```
