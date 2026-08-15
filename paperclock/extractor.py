from __future__ import annotations

import calendar
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .models import Commitment, SourceFile


MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): number for number, name in enumerate(calendar.month_abbr) if name})
MONTH_TOKEN = "|".join(sorted(MONTHS, key=len, reverse=True))

ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "expiry": ("expire", "expires", "expiry", "expiration", "valid until", "valid till", "valid upto", "valid through", "good through", "lapses"),
    "renewal": ("renew", "renews", "renewal", "auto-renew", "subscription", "membership", "recurring"),
    "cancellation": ("cancel", "cancellation", "opt out", "notice period", "terminate", "termination", "surrender"),
    "money": ("pay", "payment", "premium", "balance", "fee", "deposit", "refund", "amount due", "net pay", "total amount", "emi"),
    "submission": ("submit", "submission", "apply", "application", "file by", "respond", "response", "file tax", "filing due"),
    "appointment": ("appointment", "meeting", "hearing", "interview", "visit", "check-in", "consultation"),
    "delivery": ("deliver", "delivery", "ship", "return by", "complete", "milestone", "launch", "dispatch"),
    "warranty": ("warranty", "guarantee", "coverage", "trial", "return window", "applecare", "amc"),
    "taxes": ("tax return", "file tax", "irs filing", "1099", "w2", "w-2", "form 16", "itr", "advance tax", "tds", "gst filing", "quarterly estimate"),
    "housing": ("lease end", "lease expiry", "rent due", "rental renewal", "deposit refund", "society maintenance"),
    "utilities": ("electricity due", "energy bill", "utility payment", "broadband contract", "water bill due"),
    "banking": ("statement balance", "minimum payment", "credit card payment", "loan due", "cd maturity", "fd maturity", "emi due", "payment due date"),
    "legal": ("termination date", "nda expiry", "compliance deadline", "statute", "agreement expires"),
    "vehicle": ("vehicle registration", "registration renewal", "smog check", "mot due", "puc expiry", "fitness certificate"),
    "travel": ("passport expiry", "visa expiry", "global entry", "tsa precheck", "departure date", "valid visa"),
    "employment": ("probation end", "benefits enrollment", "fsa deadline", "hsa deadline", "salary credit", "appraisal date", "increment date"),
    "billing": ("invoice due", "remittance due", "net 30", "net-30", "payable by", "tax invoice due"),
}

ACTION_WORDS = tuple(word for words in ACTION_GROUPS.values() for word in words) + (
    "due", "deadline", "before", "by", "no later than", "remind", "schedule", "book",
    "pay", "expires", "renew", "valid until", "valid upto", "payable", "must be received",
    "last date", "last day", "maturity date", "due date", "payment due", "filing due",
    "credit date", "period ending", "as of",
)

HISTORICAL_WORDS = (
    "issued on", "created on", "updated on", "published on", "founded on", "born on", "purchased on",
    "transaction date", "invoice date", "statement date", "statement period", "bill date", "effective from", "copyright", "version",
    "changelog", "minutes from", "attended", "completed on", "paid on", "sent on", "received on",
)

INFORMATIONAL_WORDS = HISTORICAL_WORDS + (
    "printed on", "generated on", "last modified", "last updated", "release date",
    "publication date", "date of birth", "birth date", "effective date", "start date", "opening date",
)

MILESTONE_FIELDS = (
    "expiry date", "expiration date", "renewal date", "premium due date", "payment due date",
    "due date", "coverage end", "coverage ends", "policy end date", "termination date",
    "maturity date", "valid until", "valid till", "valid through", "last date", "respond by",
)

PAGE_MARKER = re.compile(r"^\[\[PAPERCLOCK_PAGE:(\d+)\]\]$")

COMMON_ENGLISH_WORDS = frozenset({
    "will", "with", "from", "that", "this", "have", "been", "were", "they", "your",
    "also", "when", "more", "most", "make", "what", "which", "their", "about", "other",
    "only", "some", "time", "very", "even", "such", "than", "then", "into", "just",
    "over", "also", "well", "like", "used", "each", "both", "must", "same", "many",
    "choice", "beginners", "development", "program", "code", "file", "text", "page",
})

ENTITY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("form 16", "form 16a", "itr", "itr-v", "income tax return", "advance tax", "tds return", "gstin", "1099-misc", "w-2 form", "form 1040"), "Tax Filing"),
    (("bank statement", "account statement", "hdfc bank", "icici bank", "state bank of india", "axis bank", "kotak bank", "chase bank", "citibank", "bank of america", "barclays"), "Bank Statement"),
    (("salary slip", "payslip", "pay stub", "wage slip", "earnings statement"), "Salary Slip"),
    (("health insurance", "medical insurance", "health policy", "new india assurance", "star health", "hdfc ergo", "care health"), "Health Insurance"),
    (("motor insurance", "vehicle insurance", "car insurance", "auto insurance"), "Vehicle Insurance"),
    (("life insurance", "term life", "lic policy", "max life"), "Life Insurance"),
    (("travel insurance",), "Travel Insurance"),
    (("home insurance", "property insurance"), "Home Insurance"),
    (("lease agreement", "rental agreement", "lease deed", "tenancy agreement"), "Lease"),
    (("fixed deposit", "fd advice", "recurring deposit"), "Fixed Deposit"),
    (("loan agreement", "home loan", "personal loan", "car loan", "mortgage loan", "emi statement"), "Loan Agreement"),
    (("credit card statement", "credit card payment"), "Credit Card"),
    (("mobile number", "mobile bill", "sim card", "prepaid mobile", "postpaid plan", "airtel broadband", "jio fiber"), "SIM Plan"),
    (("energy plan", "electricity bill", "gas plan", "power bill", "water bill"), "Energy Plan"),
    (("passport", "passport renewal"), "Passport"),
    (("travel visa", "e-visa", "visa application"), "Travel Visa"),
    (("driver licence", "driver's licence", "driving license"), "Driver’s Licence"),
    (("vehicle registration", "rc book", "puc certificate"), "Vehicle Registration"),
    (("subscription renewal", "saas membership", "annual membership"), "Subscription"),
    (("warranty certificate", "applecare", "extended warranty"), "Warranty"),
    (("certification", "professional license"), "Certification"),
    (("contract", "nda agreement", "service agreement"), "Agreement"),
    (("tax invoice", "commercial invoice", "invoice due"), "Invoice"),
    (("appointment", "doctor consultation"), "Appointment"),
    (("visa application", "job application"), "Application"),
    (("delivery shipment", "package delivery"), "Delivery"),
)

CATEGORY_TITLES = {
    "expiry": "Expiry",
    "renewal": "Renewal",
    "cancellation": "Cancellation Deadline",
    "money": "Payment Due",
    "submission": "Submission Deadline",
    "appointment": "Appointment",
    "delivery": "Delivery Due",
    "warranty": "Coverage Ends",
    "taxes": "Tax Deadline / Filing",
    "housing": "Lease Milestone",
    "insurance": "Insurance Policy Renewal",
    "subscription": "Subscription Renewal",
    "utilities": "Utility Bill Due",
    "banking": "Statement / Payment Due",
    "legal": "Legal / Contract Deadline",
    "vehicle": "Vehicle Milestone",
    "travel": "Passport / Travel Expiry",
    "employment": "Payroll / Employment Notice",
    "billing": "Invoice Due",
    "action": "Action Due",
}

NOTICE_WINDOW_PATTERN = re.compile(
    r"(?:at\s+least|within|requires?|give|prior\s+to|notice\s+of)?\s*(\d{1,3})\s*(?:business\s+)?(day|week|month)s?\s*(?:prior|before|advance|notice)",
    re.IGNORECASE,
)

PERIODICITY_PATTERN = re.compile(
    r"\b(monthly|per\s+month|/mo|p\.m\.|annually|per\s+annum|annual|/yr|p\.a\.|per\s+year|quarterly|one-time|one\s+time|per\s+period)\b",
    re.IGNORECASE,
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
    rejections: dict[str, int]


def extract_commitments(source: SourceFile, today: date, month_first: bool = False) -> Extraction:
    results: list[Commitment] = []
    rejections: Counter[str] = Counter()
    candidates = 0
    anchor = _parse_anchor(source.modified) or today
    page_number: int | None = None

    doc_entity = _extract_document_entity(source.content, source.path)

    raw_lines = source.content.splitlines()
    for line_number, raw_line in enumerate(raw_lines, start=1):
        line = re.sub(r"\s+", " ", raw_line).strip()
        marker = PAGE_MARKER.fullmatch(line)
        if marker:
            page_number = int(marker[1])
            continue
        if not line:
            continue
        segments = [line] if len(line) <= 2_500 else [s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]
        for segment in segments:
            if not segment:
                continue
            hits = _find_dates(segment, today=today, anchor=anchor, month_first=month_first)
            candidates += len(hits)
            context = _neighbor_context(raw_lines, line_number - 1)
            for hit_index, hit in enumerate(hits):
                if _is_range_start(segment, hit_index, len(hits)):
                    rejections["historical_noise"] += 1
                    continue
                commitment, rejected_as = _classify(
                    source,
                    line_number,
                    segment,
                    hit,
                    today,
                    page_number,
                    doc_entity=doc_entity,
                    context=context,
                )
                if commitment:
                    results.append(commitment)
                elif rejected_as:
                    rejections[rejected_as] += 1

    return Extraction(results, candidates, dict(rejections))


def _find_dates(text: str, today: date, anchor: date, month_first: bool) -> list[DateHit]:
    hits: list[DateHit] = []
    occupied: list[tuple[int, int]] = []

    def add(match: re.Match[str], value: date | None, quality: float, ambiguous: bool = False) -> None:
        if value is None or any(match.start() < end and match.end() > start for start, end in occupied):
            return
        hits.append(DateHit(value, match.group(0), match.start(), quality, ambiguous))
        occupied.append(match.span())

    # ISO YYYY-MM-DD or YYYY/MM/DD
    for match in re.finditer(r"\b(20\d{2})[-/.](0?[1-9]|1[0-2])[-/.](0?[1-9]|[12]\d|3[01])\b", text):
        add(match, _safe_date(int(match[1]), int(match[2]), int(match[3])), 0.95)

    # Named month formats: "August 15, 2026", "15 Aug 2026", "15-Aug-2026", "15/Aug/2026"
    named_patterns = (
        rf"\b({MONTH_TOKEN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d{{2}}))?\b",
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?:[\s\-/]+(?:of\s+)?)({MONTH_TOKEN})\.?(?:[\s\-/]+(20\d{{2}}))?\b",
    )
    for index, pattern in enumerate(named_patterns):
        for match in re.finditer(pattern, text, flags=re.I):
            if index == 0:
                month, day, year = MONTHS[match[1].lower()], int(match[2]), match[3]
            else:
                day, month, year = int(match[1]), MONTHS[match[2].lower()], match[3]
            value = _future_date(int(year) if year else None, month, day, anchor or today)
            add(match, value, 0.92 if year else 0.72)

    # Numeric DD/MM/YYYY or MM/DD/YYYY
    for match in re.finditer(r"(?<![\d/.-])(0?[1-9]|[12]\d|3[01])[/.-](0?[1-9]|[12]\d|3[01])[/.-](20\d{2})(?!\d)", text):
        first, second, year = map(int, match.groups())
        ambiguous = first <= 12 and second <= 12
        day, month = (second, first) if month_first else (first, second)
        add(match, _safe_date(year, month, day), 0.78 if ambiguous else 0.86, ambiguous)

    # Relative days / weeks / months
    relative_pattern = r"\b(?:in|within|before|by|due\s+in)\s+(\d{1,3})\s+(business\s+)?(day|week|month)s?\b"
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


def _classify(
    source: SourceFile,
    line_number: int,
    line: str,
    hit: DateHit,
    today: date,
    page_number: int | None,
    doc_entity: str | None = None,
    context: str | None = None,
) -> tuple[Commitment | None, str | None]:
    lowered = line.lower()
    context_lowered = (context or line).lower()
    if _is_reference_example(lowered):
        return None, "reference_example"
    if source.domain == "banking" and _is_bank_transaction_row(line):
        return None, "historical_noise"
    if source.domain == "banking" and "statement" in lowered and "period" in lowered:
        return None, "historical_noise"
    nearest_action, distance = _nearest_keyword(lowered, hit.start, ACTION_WORDS)
    if nearest_action is None:
        contextual_action, _ = _nearest_keyword(context_lowered, 0, ACTION_WORDS + MILESTONE_FIELDS)
        if contextual_action:
            nearest_action, distance = contextual_action, 120
    field_action = any(field in context_lowered for field in MILESTONE_FIELDS)
    nearest_history, history_distance = _nearest_keyword(lowered, hit.start, INFORMATIONAL_WORDS)
    has_action = (nearest_action is not None and distance <= 160) or field_action
    has_history = nearest_history is not None and history_distance <= 60
    local_context = lowered[max(0, hit.start - 55):hit.start + len(hit.raw) + 55]

    if hit.value < today - timedelta(days=45) and not _is_unresolved_past_commitment(lowered):
        return None, "historical_noise"
    if has_history and history_distance <= distance and not any(kw in local_context for kw in ("due", "expires", "valid", "renew", "filing", "maturity", "pay")):
        return None, "historical_noise"
    if not has_action:
        return None, "no_actionable_commitment"

    score = hit.quality * 0.46
    score += 0.38 if has_action else 0.20
    score += 0.08 if hit.value >= today else -0.02
    score -= 0.38 if has_history and (not has_action or history_distance <= distance) else 0.0
    score -= 0.12 if hit.ambiguous else 0.0
    if score < 0.42:
        return None, "no_actionable_commitment"

    category = _category(lowered, source.domain)
    confidence = max(45, min(98, round(score * 100)))
    title = _title(line, category, source.domain)

    periodicity = _extract_periodicity(line)
    notice_days, action_date = _extract_notice_window(line, hit.value)

    entity = doc_entity or _extract_entity_hint(line)

    summary = _build_summary(
        title=title,
        date_val=hit.value,
        periodicity=periodicity,
        entity=entity,
        notice_days=notice_days,
        action_date=action_date,
    )

    reason = (
        f"“{nearest_action}” appears near this date"
        if nearest_action
        else "Milestone date found in document context"
    )
    digest = hashlib.blake2s(
        f"{source.document_id or source.path}:{line_number}:{hit.value.isoformat()}:{hit.raw}".encode(), digest_size=6
    ).hexdigest()

    commitment = Commitment(
        id=digest,
        date=hit.value.isoformat(),
        title=title,
        category=category,
        source=source.path,
        line=line_number,
        snippet=line[:280],
        confidence=confidence,
        reason=reason,
        original=hit.raw,
        ambiguous=hit.ambiguous,
        page=page_number,
        document_id=source.document_id,
        domain=source.domain,
        entity=entity,
        periodicity=periodicity,
        summary=summary,
        notice_days=notice_days,
        action_date=action_date.isoformat() if action_date else None,
    )
    return commitment, None


def _nearest_keyword(text: str, position: int, words: tuple[str, ...]) -> tuple[str | None, int]:
    nearest: tuple[str | None, int] = (None, 10_000)
    for word in words:
        for match in re.finditer(rf"(?<!\w){re.escape(word)}(?!\w)", text):
            distance = min(abs(position - match.start()), abs(position - match.end()))
            if distance < nearest[1]:
                nearest = (word, distance)
    return nearest


def _category(text: str, domain: str | None = None) -> str:
    best = domain or "action"
    best_position = len(text) + 1
    action_categories = {"expiry", "renewal", "cancellation", "money", "submission", "appointment", "delivery", "warranty"}
    for category, words in ACTION_GROUPS.items():
        if domain and category not in action_categories:
            continue
        positions = [text.find(word) for word in words if word in text]
        if positions and min(positions) < best_position:
            best, best_position = category, min(positions)
    return best


def _title(line: str, category: str, domain: str | None = None) -> str:
    context = line.lower()
    entity = next(
        (label for needles, label in ENTITY_HINTS if any(needle in context for needle in needles)),
        None,
    )
    if not entity:
        domain_labels = {
            "taxes": "Tax Filing / Return",
            "housing": "Lease / Rent",
            "insurance": "Insurance Policy",
            "subscription": "Subscription",
            "utilities": "Utility Bill",
            "banking": "Bank Account Statement",
            "warranty": "Warranty",
            "legal": "Agreement",
            "vehicle": "Vehicle Milestone",
            "travel": "Travel / Passport",
            "employment": "Salary Slip / Payslip",
            "billing": "Invoice",
        }
        entity = domain_labels.get(domain or "", "Document")

    action = CATEGORY_TITLES.get(category, CATEGORY_TITLES.get("action", "Action Due"))
    if entity.casefold() == action.casefold() or entity.casefold().endswith(action.casefold()):
        return entity
    return f"{entity} {action}"


def _extract_periodicity(text: str) -> str | None:
    match = PERIODICITY_PATTERN.search(text)
    if not match:
        return None
    token = match.group(1).lower()
    if "month" in token or "/mo" in token or "p.m." in token:
        return "monthly"
    if "annu" in token or "year" in token or "/yr" in token or "p.a." in token:
        return "annual"
    if "quarter" in token:
        return "quarterly"
    if "one" in token:
        return "one-time"
    return token


def _extract_notice_window(text: str, target_date: date) -> tuple[int | None, date | None]:
    match = NOTICE_WINDOW_PATTERN.search(text)
    if not match:
        return None, None
    try:
        qty = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "day":
            days = qty
        elif unit == "week":
            days = qty * 7
        elif unit == "month":
            days = qty * 30
        else:
            days = qty
        if 1 <= days <= 180:
            action_date = target_date - timedelta(days=days)
            return days, action_date
    except (OverflowError, ValueError):
        return None, None
    return None, None


def _extract_entity_hint(text: str) -> str | None:
    for needles, label in ENTITY_HINTS:
        for needle in needles:
            # Match whole words only
            if re.search(rf"\b{re.escape(needle)}\b", text, re.I):
                return label
    return None


def _extract_document_entity(content: str, path: str) -> str | None:
    sample_lines = [line.strip() for line in content.splitlines()[:15] if line.strip()]
    for line in sample_lines:
        if line.lower().startswith("subject:"):
            subj = line[8:].strip()
            if subj:
                return subj[:70]
    stem = re.sub(r"[_-]+", " ", re.sub(r"\.[^.]+$", "", path.split("/")[-1])).strip()
    return stem.title() if stem else None


def _build_summary(
    title: str,
    date_val: date,
    periodicity: str | None,
    entity: str | None,
    notice_days: int | None,
    action_date: date | None,
) -> str:
    parts: list[str] = [title]
    if entity and entity not in title:
        parts.append(f"({entity})")
    if notice_days and action_date:
        parts.append(f"• Notice: {notice_days}d prior (Action by {action_date.strftime('%b %d')})")
    return " ".join(parts)


def _is_reference_example(text: str) -> bool:
    return any(
        re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text)
        for marker in (
            "example", "for example", "for instance", "sample", "suppose", "imagine",
            "exercise", "tutorial", "case study", "hypothetical", "demo", "illustration",
        )
    )


def _is_unresolved_past_commitment(text: str) -> bool:
    return any(
        marker in text
        for marker in ("overdue", "past due", "remains due", "still due", "unpaid", "outstanding", "action required")
    )


def _is_bank_transaction_row(text: str) -> bool:
    has_leading_date = bool(re.match(r"^\s*(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})", text))
    has_amount = bool(re.search(r"\d[\d,]*\.\d{2}", text))
    has_row_shape = "|" in text or "\t" in text or bool(re.search(r"\b(?:CR|DR|debit|credit)\b", text, re.I))
    return has_leading_date and has_amount and has_row_shape


def _neighbor_context(lines: list[str], index: int) -> str:
    nearby: list[str] = []
    for raw in lines[max(0, index - 1):min(len(lines), index + 2)]:
        cleaned = re.sub(r"\s+", " ", raw).strip()
        if cleaned and not PAGE_MARKER.fullmatch(cleaned):
            nearby.append(cleaned)
    return " · ".join(nearby)


def _is_range_start(line: str, hit_index: int, hit_count: int) -> bool:
    if hit_count < 2 or hit_index != 0:
        return False
    lowered = line.casefold()
    return any(marker in lowered for marker in (
        "period of insurance", "coverage period", "policy period", "valid from", "effective from",
        "from ", "between ", "start date", "commencement date", "for period", "statement period",
    )) and any(separator in lowered for separator in (" to ", " through ", " until ", " - "))


def _future_date(year: int | None, month: int, day: int, reference_date: date) -> date | None:
    if year:
        return _safe_date(year, month, day)
    candidate = _safe_date(reference_date.year, month, day)
    if candidate and candidate < reference_date - timedelta(days=14):
        candidate = _safe_date(reference_date.year + 1, month, day)
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
