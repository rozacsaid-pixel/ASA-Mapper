"""The form builder's ASA export format: one `field_key-->asa` or `field_key#option_key-->asa` per line."""
from __future__ import annotations

from typing import Dict, List

from .models import ParsedForm

ARROW = "-->"
DONE = {"accepted", "changed"}


class AsaExportError(ValueError):
    """The ASA export text is malformed."""


def parse_asa_export(text: str) -> Dict[str, str]:
    mapping = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        key, sep, asa = line.partition(ARROW)
        if not sep or not key.strip() or not asa.strip():
            raise AsaExportError(f"line {number} is not 'key-->asa': {line[:80]}")
        mapping[key.strip()] = asa.strip()
    return mapping


def apply_asa_export(form: ParsedForm, mapping: Dict[str, str]) -> List[str]:
    """Make the system export the source of existing ASAs. Returns mapping keys not found in the form."""
    found = set()
    for field in form.fields:
        field.existing_asa = [mapping[field.key]] if field.key in mapping else []
        if field.key in mapping:
            found.add(field.key)
        for option in field.options:
            key = f"{field.key}#{option.key}"
            option.existing_asa = [mapping[key]] if key in mapping else []
            if key in mapping:
                found.add(key)
    return sorted(k for k in mapping if k not in found)


def format_asa_export(decisions: dict) -> str:
    """Accepted or changed ASAs in the system's format, fields before their options."""
    lines = []
    for key, decision in decisions.items():
        if not isinstance(decision, dict):
            continue
        if decision.get("status") in DONE and decision.get("asa"):
            lines.append(f"{key}{ARROW}{decision['asa']}")
        for opt_key, opt in (decision.get("options") or {}).items():
            if opt.get("status") in DONE and opt.get("asa"):
                lines.append(f"{key}#{opt_key}{ARROW}{opt['asa']}")
    return "".join(line + "\n" for line in lines)
