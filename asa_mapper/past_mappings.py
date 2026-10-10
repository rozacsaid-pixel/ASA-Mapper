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
