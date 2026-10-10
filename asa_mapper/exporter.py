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
