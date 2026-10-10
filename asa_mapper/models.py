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
        # The alias itself carries meaning (e.g. asa_yes_bhc_specialentities) when the summary is blank.
        alias_words = self.asa.replace("asa_", "", 1).replace("_", " ")
        return " ".join([self.section, self.group, self.summary, self.definition, self.keywords, alias_words])


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
