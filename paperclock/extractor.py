from __future__ import annotations

import calendar
import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import PurePath

from .models import Commitment, SourceFile


MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): number for number, name in enumerate(calendar.month_abbr) if name})
MONTH_TOKEN = "|".join(sorted(MONTHS, key=len, reverse=True))

ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "expiry": ("expire", "expires", "expiry", "expiration", "valid until", "good through", "lapses"),
    "renewal": ("renew", "renews", "renewal", "auto-renew", "subscription", "membership"),
    "cancellation": ("cancel", "cancellation", "opt out", "notice period", "terminate", "termination"),
    "money": ("pay", "payment", "premium", "balance", "fee", "deposit", "refund"),
    "submission": ("submit", "submission", "apply", "application", "file by", "respond", "response"),
    "appointment": ("appointment", "meeting", "hearing", "interview", "visit", "check-in"),
    "delivery": ("deliver", "delivery", "ship", "return by", "complete", "milestone", "launch"),
    "warranty": ("warranty", "guarantee", "coverage", "trial", "return window"),
}
ACTION_WORDS = tuple(word for words in ACTION_GROUPS.values() for word in words) + (
    "due", "deadline", "before", "by", "no later than", "remind", "schedule", "book",
)
HISTORICAL_WORDS = (
    "issued", "created", "updated", "published", "founded", "born", "purchased", "ordered",
    "transaction date", "invoice date", "statement date", "effective from", "copyright", "version",
    "changelog", "minutes from", "attended", "completed on", "paid on", "sent on", "received on",
)


@dataclass(frozen=True, slots=True)
class DateHit:
    value: date
    raw: str
    start: int
    quality: float
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class Extraction:
    commitments: list[Commitment]
    candidates: int


def extract_commitments(source: SourceFile, today: date, month_first: bool = False) -> Extraction:
    results: list[Commitment] = []
    candidates = 0
    anchor = _parse_anchor(source.modified) or today
    for line_number, raw_line in enumerate(source.content.splitlines(), start=1):
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or len(line) > 2_000:
            continue
        hits = _find_dates(line, today=today, anchor=anchor, month_first=month_first)
        candidates += len(hits)
        for hit in hits:
            commitment = _classify(source.path, line_number, line, hit, today)
            if commitment:
                results.append(commitment)
    return Extraction(results, candidates)


def _find_dates(text: str, today: date, anchor: date, month_first: bool) -> list[DateHit]:
    hits: list[DateHit] = []
    occupied: list[tuple[int, int]] = []

    def add(match: re.Match[str], value: date | None, quality: float, ambiguous: bool = False) -> None:
        if value is None or any(match.start() < end and match.end() > start for start, end in occupied):
            return
        hits.append(DateHit(value, match.group(0), match.start(), quality, ambiguous))
        occupied.append(match.span())

    for match in re.finditer(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b", text):
        add(match, _safe_date(int(match[1]), int(match[2]), int(match[3])), 0.95)

    named_patterns = (
        rf"\b({MONTH_TOKEN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH_TOKEN})\.?(?:,?\s+(20\d{{2}}))?\b",
    )
    for index, pattern in enumerate(named_patterns):
        for match in re.finditer(pattern, text, flags=re.I):
            if index == 0:
                month, day, year = MONTHS[match[1].lower()], int(match[2]), match[3]
            else:
                day, month, year = int(match[1]), MONTHS[match[2].lower()], match[3]
            value = _future_date(int(year) if year else None, month, day, today)
            add(match, value, 0.92 if year else 0.72)

    for match in re.finditer(r"(?<![\d/.-])(0?[1-9]|[12]\d|3[01])[/.-](0?[1-9]|[12]\d|3[01])[/.-](20\d{2})(?!\d)", text):
        first, second, year = map(int, match.groups())
        ambiguous = first <= 12 and second <= 12
        day, month = (second, first) if month_first else (first, second)
        add(match, _safe_date(year, month, day), 0.78 if ambiguous else 0.86, ambiguous)

    relative_pattern = r"\b(?:in|within)\s+(\d{1,3})\s+(business\s+)?(day|week|month)s?\b"
    for match in re.finditer(relative_pattern, text, flags=re.I):
        amount = min(int(match[1]), 730)
        unit = match[3].lower()
        if match[2]:
            value = _add_business_days(anchor, amount)
        elif unit == "day":
            value = anchor + timedelta(days=amount)
        elif unit == "week":
            value = anchor + timedelta(weeks=amount)
        else:
            value = _add_months(anchor, amount)
        add(match, value, 0.66)

    for word, offset in (("today", 0), ("tomorrow", 1)):
        for match in re.finditer(rf"\b{word}\b", text, flags=re.I):
            add(match, anchor + timedelta(days=offset), 0.58)

    hits.sort(key=lambda item: item.start)
    return hits


def _classify(path: str, line_number: int, line: str, hit: DateHit, today: date) -> Commitment | None:
    lowered = line.lower()
    nearest_action, distance = _nearest_keyword(lowered, hit.start, ACTION_WORDS)
    nearest_history, history_distance = _nearest_keyword(lowered, hit.start, HISTORICAL_WORDS)
    has_action = nearest_action is not None and distance <= 120
    has_history = nearest_history is not None and history_distance <= 80

    score = hit.quality * 0.46
    score += 0.38 if has_action else 0.0
    score += 0.08 if hit.value >= today else -0.02
    score -= 0.38 if has_history and (not has_action or history_distance <= distance) else 0.0
    score -= 0.12 if hit.ambiguous else 0.0
    if score < 0.47:
        return None

    category = _category(lowered)
    confidence = max(42, min(98, round(score * 100)))
    title = _title(line)
    reason = (
        f"“{nearest_action}” appears near this date"
        if nearest_action
        else "future date found in a deadline-like sentence"
    )
    digest = hashlib.blake2s(
        f"{path}:{line_number}:{hit.value.isoformat()}:{hit.raw}".encode(), digest_size=6
    ).hexdigest()
    return Commitment(
        id=digest,
        date=hit.value.isoformat(),
        title=title,
        category=category,
        source=path,
        line=line_number,
        snippet=line[:280],
        confidence=confidence,
        reason=reason,
        original=hit.raw,
        ambiguous=hit.ambiguous,
    )


def _nearest_keyword(text: str, position: int, words: tuple[str, ...]) -> tuple[str | None, int]:
    nearest: tuple[str | None, int] = (None, 10_000)
    for word in words:
        for match in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", text):
            distance = min(abs(position - match.start()), abs(position - match.end()))
            if distance < nearest[1]:
                nearest = (word, distance)
    return nearest


def _category(text: str) -> str:
    best = "action"
    best_position = len(text) + 1
    for category, words in ACTION_GROUPS.items():
        positions = [text.find(word) for word in words if word in text]
        if positions and min(positions) < best_position:
            best, best_position = category, min(positions)
    return best


def _title(line: str) -> str:
    title = re.sub(r"^[\s>*#\-–—\d.)]+", "", line).strip()
    title = re.sub(r"\s+", " ", title)
    if len(title) > 104:
        title = title[:101].rsplit(" ", 1)[0] + "…"
    return title or "Untitled commitment"


def _future_date(year: int | None, month: int, day: int, today: date) -> date | None:
    if year:
        return _safe_date(year, month, day)
    candidate = _safe_date(today.year, month, day)
    if candidate and candidate < today - timedelta(days=14):
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _add_business_days(value: date, days: int) -> date:
    cursor = value
    added = 0
    while added < days:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            added += 1
    return cursor


def _parse_anchor(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None
