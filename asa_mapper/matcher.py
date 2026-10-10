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
PARENT_BOOST = 0.3
GROUP_REPEAT = 1          # extra weight on the field's own group title (e.g. "Principal Place of Residence")
KEY_WORD_REPEAT = 1       # words from the field key (ssn, zip, bankname...) added to the query
OPTION_SET_WEIGHT = 0.3   # how well a field ASA's library options cover the form's options
POLARITY_RULE = True
INHERIT_PARENT_TEXT = True  # options with blank meaning borrow their parent's (asa_yes_<category> rows are blank)
CHECKBOX_PARENT_BOOST = True  # measured on v36: turning it off lowers option accuracy (82% -> 80%)
ACRONYMS = True           # "Social Security Number" also matches "SSN"
ACRONYM_SKIP = {"of", "the", "and", "or", "a", "an", "with", "for", "in", "to"}      # checkbox options and Yes/No labels never get the opposite asa_yes_/asa_no_
GENERIC_KEY_WORDS = {"textbox", "radio", "multiplecheckbox", "checkbox", "dropdown", "customformat",
                     "paragraph", "op", "questionnaire", "part", "investordatasheet"}
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


def key_words(key: str) -> str:
    return " ".join(w for w in re.split(r"[^a-z]+", (key or "").lower()) if len(w) > 1 and w not in GENERIC_KEY_WORDS)


def field_query(field: FormField) -> str:
    options = " ".join(o.label for o in field.options)
    group = " ".join(field.path[-1:])
    return " ".join([field.label, field.label, " ".join(field.headings), " ".join(field.path)]
                    + [group] * GROUP_REPEAT + [key_words(field.key)] * KEY_WORD_REPEAT + [options])


def acronym(text: str) -> str:
    words = [w for w in norm(text).split() if w not in ACRONYM_SKIP]
    return "".join(w[0] for w in words) if len(words) >= 2 else ""


def with_acronym(text: str) -> str:
    return f"{text} {acronym(text)}" if ACRONYMS else text


def polarity_conflict(asa: str, option_label: str, widget: str) -> bool:
    """A ticked checkbox means yes; a "Yes"/"No" option must not take the opposite answer."""
    label = norm(option_label)
    if asa.startswith("asa_no_") and (widget == "MultipleCheckbox" or label == "yes"):
        return True
    return asa.startswith("asa_yes_") and label == "no"


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
        self._options = [e for e in library.entries if e.kind == "option"]
        self._option_matrix = (self._vec.transform([self._option_text(e) for e in self._options])
                               if self._options else None)
        self._children: Dict[str, List[int]] = {}
        for i, e in enumerate(self._options):
            if e.parent:
                self._children.setdefault(e.parent, []).append(i)

    def _option_text(self, entry) -> str:
        parent = self.library.get(entry.parent)
        if INHERIT_PARENT_TEXT and parent and len(entry.summary) < 25:
            return f"{entry.text} {parent.summary} {parent.definition} {parent.keywords}"
        return entry.text

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
        if OPTION_SET_WEIGHT and field.options and family != "text" and self._option_matrix is not None:
            option_sims = cosine_similarity(
                self._vec.transform([with_acronym(o.label) for o in field.options]), self._option_matrix)
            for parent, idx in self._children.items():
                if parent in scores:
                    coverage = float(option_sims[:, idx].max(axis=1).mean())
                    scores[parent] += OPTION_SET_WEIGHT * coverage
                    reasons[parent] += f", options match {coverage:.2f}"

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
        """Options from the whole library; those under the field's ASA are boosted, not required.

        In real mappings a checkbox option often maps to an ASA under a different field name
        (e.g. one asa_yes_<category>_accreditedinvestor per checkbox).
        """
        if self._option_matrix is None:
            return {o.key: [] for o in field.options}
        out: Dict[str, List[Suggestion]] = {}
        for opt in field.options:
            query = " ".join([with_acronym(opt.label), opt.label, field.label])
            sims = cosine_similarity(self._vec.transform([query]), self._option_matrix)[0]
            scores: Dict[str, float] = {}
            reasons: Dict[str, str] = {}
            for entry, sim in zip(self._options, sims):
                if POLARITY_RULE and polarity_conflict(entry.asa, opt.label, field.widget):
                    continue
                under_parent = (bool(parent_asa) and entry.parent == parent_asa
                                and (CHECKBOX_PARENT_BOOST or field.widget != "MultipleCheckbox"))
                scores[entry.asa] = float(sim) + (PARENT_BOOST if under_parent else 0.0)
                reasons[entry.asa] = f"text match {sim:.2f}" + (", under field ASA" if under_parent else "")
            hits: Set[str] = set()
            for pm in self.past:
                if (pm.kind == "option" and pm.asa in scores and pm.asa not in hits
                        and _label_match(pm.label, opt.label) and _label_match(pm.field_label, field.label)):
                    hits.add(pm.asa)
                    scores[pm.asa] += PAST_BOOST_BASE + PAST_BOOST_PATH
                    reasons[pm.asa] += f", seen before in '{pm.form}'"
            out[opt.key] = _rank(scores, reasons, top_n, hits)
        return out
