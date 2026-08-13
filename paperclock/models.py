from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    content: str
    modified: str | None = None


@dataclass(frozen=True, slots=True)
class Commitment:
    id: str
    date: str
    title: str
    category: str
    source: str
    line: int
    snippet: str
    confidence: int
    reason: str
    original: str
    ambiguous: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
