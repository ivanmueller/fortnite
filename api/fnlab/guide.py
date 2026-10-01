"""
Page guides: the explanatory text shown next to each analysis in the dashboard.

The text for every page lives in analyses/guides.py, keyed by analysis id.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Guide:
    question: str                                   # the research question, one sentence
    method: list[str]                               # how it's measured, in order
    terms: dict[str, str] = field(default_factory=dict)   # metric/test name -> definition
    charts: dict[str, str] = field(default_factory=dict)  # chart title -> how to read it
    conclude: list[str] = field(default_factory=list)     # how to turn the result into a conclusion
    limits: list[str] = field(default_factory=list)       # what this page can't tell you
