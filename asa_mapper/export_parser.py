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
HELPER_LABELS = ("for splitting rule",)  # "For Mapping"/combined-address boxes carry real ASAs
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
