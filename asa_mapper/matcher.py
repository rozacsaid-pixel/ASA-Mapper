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
