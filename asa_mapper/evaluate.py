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
