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
