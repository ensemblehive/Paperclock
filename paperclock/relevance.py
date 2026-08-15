from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath


TOKEN_LIMIT = 1_200

# A document must contain corroborating evidence, not merely mention a topic. Multi-word
# phrases carry more weight because they are far less likely to occur in examples or prose.
DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "taxes": (
        "income tax return", "assessment year", "tax payable", "itr-v", "form 16",
        "form 26as", "advance tax", "gstin", "tax filing", "irs filing", "form 1040",
        "taxpayer", "tax period", "filing period", "due date",
    ),
    "housing": (
        "lease agreement", "rental agreement", "tenancy agreement", "rent due",
        "lease renewal", "security deposit", "landlord", "tenant", "lease term",
        "commencement date", "expiration date",
    ),
    "insurance": (
        "insurance policy", "policyholder", "policy number", "insurance premium",
        "coverage period", "sum insured", "policy renewal", "insurance claim",
        "health insurance", "motor insurance", "life insurance", "period of insurance",
        "policy schedule", "insured name", "name of insured", "premium due",
        "renewal premium", "policy expiry", "policy end date", "coverage ends",
        "certificate of insurance", "insurer",
    ),
    "subscription": (
        "subscription renewal", "membership renewal", "auto-renew", "billing period",
        "monthly subscription", "annual membership", "renewal date", "next billing date",
    ),
    "utilities": (
        "electricity bill", "power bill", "water bill", "gas bill", "broadband bill",
        "mobile postpaid", "utility bill", "payment due date", "billing period",
        "consumer number", "meter number",
    ),
    "banking": (
        "bank statement", "account statement", "statement of account", "statement period",
        "statement of transactions", "transaction statement", "account number", "account no",
        "opening balance", "closing balance", "transaction date", "value date",
        "withdrawal", "deposit", "debit", "credit", "available balance",
    ),
    "warranty": (
        "warranty certificate", "warranty coverage", "extended warranty", "return window",
        "replacement guarantee", "service warranty", "warranty period", "coverage end date",
    ),
    "legal": (
        "non-disclosure agreement", "employment contract", "service agreement",
        "termination notice", "notice period", "governing law", "effective date",
        "term of agreement", "expiration date", "renewal term",
    ),
    "vehicle": (
        "vehicle registration", "registration certificate", "driving licence",
        "driver licence", "puc certificate", "fitness certificate", "road tax",
        "registration expiry", "validity date",
    ),
    "travel": (
        "passport expiry", "passport renewal", "passport", "visa expiry", "travel visa",
        "flight itinerary", "departure date",
    ),
    "employment": (
        "salary slip", "pay slip", "payslip", "pay stub", "earnings statement",
        "net pay", "gross salary", "offer letter", "notice period", "pay period",
        "salary credit date", "joining date",
    ),
    "billing": (
        "invoice due", "tax invoice", "amount due", "balance due", "payment terms",
        "remittance advice", "net 30", "payable by", "invoice",
    ),
}

ACTION_TERMS = (
    "due", "deadline", "expires", "expiry", "expiration", "valid until", "valid till",
    "valid through", "renew", "renews", "renewed", "renewal", "cancel by", "cancellation", "terminate by",
    "pay by", "payment due", "submit by", "return by", "coverage ends", "file by",
    "filing due", "maturity date", "action required", "notice must", "respond by",
    "expiry date", "expiration date", "renewal date", "due date", "coverage end date",
    "policy end date", "registration expiry", "validity date",
)

REFERENCE_MARKERS = (
    "for beginners", "programming essentials", "textbook", "e-book", "ebook", "tutorial",
    "course material", "learning objectives", "chapter summary", "practice exercise",
    "sample code", "source code", "table of contents", "isbn", "publisher",
)

BANK_HEADERS = (
    "date", "description", "particulars", "narration", "transaction", "withdrawal",
    "deposit", "debit", "credit", "amount", "balance",
)


@dataclass(frozen=True, slots=True)
class Relevance:
    relevant: bool
    domain: str | None
    confidence: int = 0
    reason: str = ""


def classify_document(path: str, content: str) -> Relevance:
    sample = _sample(content)
    filename = re.sub(r"[_-]+", " ", PurePath(path).stem).casefold()
    lowered = sample.casefold()

    reference_hits = sum(_contains(lowered, marker) or _contains(filename, marker) for marker in REFERENCE_MARKERS)
    code_hits = sum(token in f" {lowered} " for token in (" def ", " class ", " import ", "print(", "python ", "javascript "))
    if reference_hits >= 2 or (reference_hits >= 1 and code_hits >= 1):
        return Relevance(False, None, 0, "reference or educational material")

    bank_score = _bank_signature_score(lowered)
    if bank_score >= 6:
        return Relevance(True, "banking", min(99, 72 + bank_score * 3), "bank statement structure")

    scores: dict[str, int] = {}
    content_scores: dict[str, int] = {}
    filename_scores: dict[str, int] = {}
    for domain, terms in DOMAIN_TERMS.items():
        content_hits = {_normalize(term) for term in terms if _contains(lowered, term)}
        filename_hits = {_normalize(term) for term in terms if _contains(filename, term)}
        content_scores[domain] = sum(2 if " " in term else 1 for term in content_hits)
        filename_scores[domain] = min(3, sum(2 if " " in term else 1 for term in filename_hits))
        scores[domain] = content_scores[domain] + filename_scores[domain]

    domain, score = max(scores.items(), key=lambda item: item[1])
    cohesive = _has_cohesive_commitment(content, DOMAIN_TERMS[domain])
    has_action = any(_contains(lowered, term) for term in ACTION_TERMS)
    has_date = _has_date(lowered)
    structural = content_scores[domain] >= 4 and has_action and has_date
    filename_corroborated = filename_scores[domain] >= 2 and content_scores[domain] >= 2 and has_action and has_date
    # Content must corroborate the filename. A filename alone is never treated as evidence.
    relevant = (cohesive and score >= 2) or structural or filename_corroborated
    confidence = min(96, 54 + score * 7) if relevant else 0
    return Relevance(relevant, domain if relevant else None, confidence, "cohesive document evidence" if relevant else "insufficient corroborating evidence")


def is_bank_statement(content: str) -> bool:
    return _bank_signature_score(_sample(content).casefold()) >= 6


def _bank_signature_score(text: str) -> int:
    identity = sum(_contains(text, marker) for marker in (
        "bank statement", "account statement", "statement of account", "statement period",
        "statement of transactions", "transaction statement", "account number", "account no",
    ))
    balances = sum(_contains(text, marker) for marker in ("opening balance", "closing balance", "available balance"))
    headers = sum(_contains(text, marker) for marker in BANK_HEADERS)
    dated_rows = len(re.findall(r"(?m)^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}.*\d[\d,]*(?:\.\d{2})", text))
    return identity * 3 + min(2, balances) * 2 + min(4, headers) + min(3, dated_rows)


def _has_cohesive_commitment(content: str, domain_terms: tuple[str, ...]) -> bool:
    blocks = [re.sub(r"\s+", " ", block).casefold() for block in re.split(r"[\n.!?]+", content) if block.strip()]
    for block in blocks:
        if len(block) > 800:
            continue
        has_domain = any(_contains(block, term) for term in domain_terms)
        has_action = any(_contains(block, term) for term in ACTION_TERMS)
        has_date = _has_date(block)
        if has_domain and has_action and has_date:
            return True
    return False


def _has_date(text: str) -> bool:
    return bool(re.search(
        r"\b(?:20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]20\d{2}|"
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2})\b",
        text,
        re.I,
    ))


def _contains(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(_normalize(term))}(?!\w)", text, re.I))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _sample(content: str) -> str:
    tokens = list(re.finditer(r"\S+", content))
    if len(tokens) <= TOKEN_LIMIT:
        return content
    return content[:tokens[TOKEN_LIMIT - 1].end()]
