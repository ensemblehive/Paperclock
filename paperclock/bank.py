from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .relevance import is_bank_statement


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Salary & Income": ("salary", "payroll", "wage", "stipend", "bonus", "dividend", "interest credit", "direct dep", "neft cr", "ach credit"),
    "Rent & Housing": ("rent", "landlord", "society maint", "maintenance fee", "hoa dues", "property tax"),
    "Utilities & Bills": ("electricity", "power", "water board", "gas bill", "broadband", "fiber", "airtel", "jio", "verizon", "utility", "billdesk"),
    "Subscriptions": ("netflix", "spotify", "apple.com", "prime", "youtube", "github", "google one", "hotstar", "adobe", "openai", "icloud", "dropbox"),
    "Food & Groceries": ("swiggy", "zomato", "blinkit", "zepto", "instamart", "uber eats", "doordash", "supermarket", "grocery", "restaurant", "cafe"),
    "Shopping": ("amazon", "flipkart", "myntra", "target", "walmart", "apple store", "paypal", "ebay", "retail", "pos purchase"),
    "Travel & Commute": ("uber", "ola", "lyft", "flight", "airline", "irctc", "railway", "petrol", "fuel", "shell", "metro", "toll", "fastag", "parking"),
    "Investments & Savings": ("zerodha", "groww", "vanguard", "fidelity", "mutual fund", "sip", "fixed deposit", "stock", "equity", "crypto", "rd debit"),
    "EMI & Loans": ("emi", "loan repayment", "home loan", "car loan", "mortgage", "credit card payment"),
}

HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "transaction date", "txn date", "value date", "posting date"),
    "description": ("description", "particulars", "narration", "transaction details", "details", "memo"),
    "debit": ("debit", "debit amount", "withdrawal", "withdrawal amount", "withdrawal amt", "withdrawals", "money out", "paid out", "dr amount"),
    "credit": ("credit", "credit amount", "deposit", "deposit amount", "deposit amt", "deposits", "money in", "paid in", "cr amount"),
    "amount": ("amount", "transaction amount", "txn amount"),
    "type": ("type", "cr/dr", "dr/cr", "transaction type"),
    "balance": ("balance", "running balance", "closing balance"),
}

DATE_FORMATS = (
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %b %y",
)


@dataclass(frozen=True, slots=True)
class BankTransaction:
    date: str
    description: str
    amount: float
    tx_type: str
    category: str
    balance: float | None = None


@dataclass(frozen=True, slots=True)
class BankStatementSummary:
    statement_start: str
    statement_end: str
    opening_balance: float | None
    closing_balance: float | None
    balance_change: float | None
    total_income: float
    total_expense: float
    net_cashflow: float
    transaction_count: int
    credit_count: int
    debit_count: int
    average_expense: float
    currency: str
    categories: list[dict[str, object]]
    top_expenses: list[dict[str, object]]
    largest_credit: dict[str, object] | None
    recurring_payments: list[dict[str, object]]
    verification: str
    rows_rejected: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def analyze_bank_statement(content: str, default_currency: str = "$") -> BankStatementSummary | None:
    if not is_bank_statement(content):
        return None

    transactions, rejected = _parse_table(content)
    if len(transactions) < 2:
        return None

    currency = _detect_currency(content, default_currency)
    total_income = sum(item.amount for item in transactions if item.tx_type == "credit")
    total_expense = sum(item.amount for item in transactions if item.tx_type == "debit")
    credits = [item for item in transactions if item.tx_type == "credit"]
    debits = [item for item in transactions if item.tx_type == "debit"]
    transaction_dates = sorted(item.date for item in transactions)
    opening_balance, closing_balance = _statement_balances(content, transactions)
    categories = _category_totals(transactions, total_expense)
    top_expenses = [
        {
            "date": item.date,
            "description": item.description,
            "amount": round(item.amount, 2),
            "category": item.category,
        }
        for item in sorted(
            (item for item in transactions if item.tx_type == "debit"),
            key=lambda item: item.amount,
            reverse=True,
        )[:5]
    ]
    largest_credit_item = max(credits, key=lambda item: item.amount) if credits else None
    largest_credit = (
        {
            "date": largest_credit_item.date,
            "description": largest_credit_item.description,
            "amount": round(largest_credit_item.amount, 2),
        }
        if largest_credit_item else None
    )

    return BankStatementSummary(
        statement_start=transaction_dates[0],
        statement_end=transaction_dates[-1],
        opening_balance=round(opening_balance, 2) if opening_balance is not None else None,
        closing_balance=round(closing_balance, 2) if closing_balance is not None else None,
        balance_change=round(closing_balance - opening_balance, 2) if opening_balance is not None and closing_balance is not None else None,
        total_income=round(total_income, 2),
        total_expense=round(total_expense, 2),
        net_cashflow=round(total_income - total_expense, 2),
        transaction_count=len(transactions),
        credit_count=len(credits),
        debit_count=len(debits),
        average_expense=round(total_expense / len(debits), 2) if debits else 0.0,
        currency=currency,
        categories=categories,
        top_expenses=top_expenses,
        largest_credit=largest_credit,
        recurring_payments=_recurring_payments(debits),
        verification=_verify_running_balances(transactions, opening_balance, closing_balance),
        rows_rejected=rejected,
    )


def _parse_table(content: str) -> tuple[list[BankTransaction], int]:
    lines = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("[[PAPERCLOCK_PAGE:")]
    transactions: list[BankTransaction] = []
    rejected = 0
    header: dict[str, int] | None = None
    delimiter: str | None = None

    for line in lines:
        candidate_delimiter = _delimiter_for(line)
        cells = _split_row(line, candidate_delimiter)
        detected = _map_headers(cells)
        if "date" in detected and "description" in detected and ({"debit", "credit"} & detected.keys() or "amount" in detected):
            header, delimiter = detected, candidate_delimiter
            continue
        if not header:
            tx = _parse_flagged_row(line)
        else:
            row = _split_row(line, delimiter)
            tx = _transaction_from_columns(row, header)
        if tx:
            transactions.append(tx)
        elif header and _looks_like_transaction(line):
            rejected += 1

    unique: dict[tuple[str, str, float, str], BankTransaction] = {}
    for transaction in transactions:
        key = (transaction.date, re.sub(r"\s+", " ", transaction.description.casefold()), transaction.amount, transaction.tx_type)
        unique.setdefault(key, transaction)
    return list(unique.values()), rejected


def _transaction_from_columns(row: list[str], header: dict[str, int]) -> BankTransaction | None:
    date_text = _cell(row, header.get("date"))
    normalized_date = _normalize_date(date_text)
    if not normalized_date:
        return None
    description = _cell(row, header.get("description"))[:100].strip()
    if not description:
        return None

    debit = _parse_number(_cell(row, header.get("debit")))
    credit = _parse_number(_cell(row, header.get("credit")))
    amount = _parse_number(_cell(row, header.get("amount")))
    type_token = _cell(row, header.get("type")).casefold()

    if credit is not None and debit is None:
        value, tx_type = credit, "credit"
    elif debit is not None and credit is None:
        value, tx_type = debit, "debit"
    elif amount is not None:
        value = abs(amount)
        if amount < 0 or re.search(r"\b(?:dr|debit|withdrawal)\b", type_token):
            tx_type = "debit"
        elif re.search(r"\b(?:cr|credit|deposit)\b", type_token):
            tx_type = "credit"
        else:
            return None
    else:
        return None

    if value <= 0:
        return None
    balance = _parse_number(_cell(row, header.get("balance")))
    return BankTransaction(normalized_date, description, value, tx_type, _categorize(description, tx_type), balance)


def _parse_flagged_row(line: str) -> BankTransaction | None:
    delimiter = _delimiter_for(line)
    cells = _split_row(line, delimiter)
    if len(cells) < 3:
        return None
    normalized_date = _normalize_date(cells[0])
    if not normalized_date:
        return None

    combined = next((
        (index, match)
        for index, cell in enumerate(cells[1:], 1)
        if (match := re.fullmatch(r"\s*([^A-Za-z]*\d[^A-Za-z]*)\s*(CR|DR|CREDIT|DEBIT)\s*", cell, re.I))
    ), None)
    if combined:
        amount_index, match = combined
        amount = _parse_number(match.group(1))
        if amount is None:
            return None
        description = " ".join(cell for index, cell in enumerate(cells[1:], 1) if index != amount_index).strip()[:100]
        tx_type = "credit" if match.group(2).casefold() in {"cr", "credit"} else "debit"
        return BankTransaction(normalized_date, description or "Transaction", abs(amount), tx_type, _categorize(description, tx_type))

    flag_index = next((index for index, cell in enumerate(cells[1:], 1) if re.fullmatch(r"(?:CR|DR|CREDIT|DEBIT)", cell.strip(), re.I)), None)
    if flag_index is None:
        return None
    numeric = [(index, _parse_number(cell)) for index, cell in enumerate(cells[1:], 1)]
    numeric = [(index, value) for index, value in numeric if value is not None]
    if not numeric:
        return None
    amount_index, amount = min(numeric, key=lambda item: abs(item[0] - flag_index))
    description = " ".join(cell for index, cell in enumerate(cells[1:], 1) if index not in {flag_index, amount_index}).strip()[:100]
    tx_type = "credit" if cells[flag_index].strip().casefold() in {"cr", "credit"} else "debit"
    return BankTransaction(normalized_date, description or "Transaction", abs(amount), tx_type, _categorize(description, tx_type))


def _map_headers(cells: list[str]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    for index, cell in enumerate(cells):
        normalized = _header(cell)
        for field, aliases in HEADER_ALIASES.items():
            if field not in mapped and normalized in aliases:
                mapped[field] = index
                break
    return mapped


def _delimiter_for(line: str) -> str:
    counts = {delimiter: line.count(delimiter) for delimiter in ("|", "\t", ",")}
    delimiter, count = max(counts.items(), key=lambda item: item[1])
    if count >= 2:
        return delimiter
    if len(re.split(r"\s{2,}", line.strip())) >= 3:
        return "__SPACE__"
    return "|"


def _split_row(line: str, delimiter: str) -> list[str]:
    if delimiter == ",":
        try:
            return next(csv.reader(io.StringIO(line), skipinitialspace=True))
        except (csv.Error, StopIteration):
            return []
    if delimiter == "__SPACE__":
        return [cell.strip() for cell in re.split(r"\s{2,}", line.strip())]
    return [cell.strip() for cell in line.split(delimiter)]


def _normalize_date(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value.strip())
    if re.fullmatch(r"\d{5}(?:\.0+)?", cleaned):
        serial = int(float(cleaned))
        if 20_000 <= serial <= 80_000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date().isoformat()
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_number(value: str) -> float | None:
    text = value.strip()
    if not text or text.casefold() in {"na", "n/a", "-", "--"}:
        return None
    negative = (text.startswith("(") and text.endswith(")")) or text.startswith("-")
    cleaned = re.sub(r"[^\d.]", "", text.replace(",", ""))
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return -number if negative else number


def _verify_running_balances(
    transactions: list[BankTransaction],
    opening_balance: float | None,
    closing_balance: float | None,
) -> str:
    if opening_balance is not None and closing_balance is not None:
        credits = sum(item.amount for item in transactions if item.tx_type == "credit")
        debits = sum(item.amount for item in transactions if item.tx_type == "debit")
        if abs(opening_balance + credits - debits - closing_balance) <= 0.05:
            return "verified"
    balanced_pairs = 0
    checked_pairs = 0
    previous = None
    for transaction in transactions:
        if transaction.balance is None:
            previous = None
            continue
        if previous is not None:
            expected = previous + transaction.amount if transaction.tx_type == "credit" else previous - transaction.amount
            checked_pairs += 1
            if abs(expected - transaction.balance) <= 0.02:
                balanced_pairs += 1
        previous = transaction.balance
    if checked_pairs == 0:
        return "discrepancy" if opening_balance is not None and closing_balance is not None else "unverifiable"
    return "verified" if balanced_pairs / checked_pairs >= 0.8 else "discrepancy"


def _statement_balances(content: str, transactions: list[BankTransaction]) -> tuple[float | None, float | None]:
    opening = _named_amount(content, "opening balance")
    closing = _named_amount(content, "closing balance")
    balanced = [item for item in transactions if item.balance is not None]
    if balanced:
        first = balanced[0]
        last = balanced[-1]
        if opening is None:
            opening = first.balance - first.amount if first.tx_type == "credit" else first.balance + first.amount
        if closing is None:
            closing = last.balance
    return opening, closing


def _named_amount(content: str, label: str) -> float | None:
    match = re.search(
        rf"(?i)\b{re.escape(label)}\b\s*(?:[:=-]|is)?\s*(?:₹|Rs\.?|INR|\$|USD|€|EUR|£|GBP)?\s*"
        r"(\(?-?\d[\d,]*(?:\.\d{1,2})?\)?)",
        content,
    )
    return _parse_number(match.group(1)) if match else None


def _recurring_payments(debits: list[BankTransaction]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, float], list[BankTransaction]] = {}
    for item in debits:
        normalized = re.sub(r"\b\d+\b|[^a-z ]+", " ", item.description.casefold())
        normalized = re.sub(r"\b(?:upi|pos|card|payment|transfer|debit|txn|ref)\b", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if len(normalized) >= 3:
            grouped.setdefault((normalized[:60], round(item.amount, 2)), []).append(item)
    recurring = [
        {
            "description": items[0].description,
            "amount": round(amount, 2),
            "count": len(items),
        }
        for (_, amount), items in grouped.items()
        if len(items) >= 2
    ]
    return sorted(recurring, key=lambda item: float(item["amount"]) * int(item["count"]), reverse=True)[:5]


def _category_totals(transactions: list[BankTransaction], total_expense: float) -> list[dict[str, object]]:
    totals: dict[str, list[float]] = {}
    for transaction in transactions:
        if transaction.tx_type == "debit":
            totals.setdefault(transaction.category, []).append(transaction.amount)
    result = [
        {
            "category": category,
            "amount": round(sum(amounts), 2),
            "percentage": round(sum(amounts) / total_expense * 100, 1) if total_expense else 0.0,
            "count": len(amounts),
        }
        for category, amounts in totals.items()
    ]
    return sorted(result, key=lambda item: float(item["amount"]), reverse=True)


def _detect_currency(content: str, default: str) -> str:
    lowered = content.casefold()
    if any(token in lowered for token in ("inr", "₹", "rs.", "hdfc", "icici", "sbi bank", "axis bank")):
        return "₹"
    if "€" in content or "eur" in lowered:
        return "€"
    if "£" in content or "gbp" in lowered:
        return "£"
    return "$" if "$" in content or "usd" in lowered else default


def _categorize(description: str, tx_type: str) -> str:
    if tx_type == "credit":
        return "Salary & Income"
    lowered = description.casefold()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return category
    return "Other Expenses"


def _header(value: str) -> str:
    return re.sub(r"[^a-z0-9/]+", " ", value.casefold()).strip()


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def _looks_like_transaction(line: str) -> bool:
    first = _split_row(line, _delimiter_for(line))[0] if line else ""
    return _normalize_date(first) is not None
