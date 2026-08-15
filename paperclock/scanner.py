from __future__ import annotations

from datetime import date
import hashlib

from .bank import analyze_bank_statement
from .extractor import extract_commitments
from .models import Commitment, SourceFile
from .readers import UnreadableFile, decode_upload
from .policy import (
    DUPLICATE_COMMITMENT, HISTORICAL_NOISE, IRRELEVANT_DOCUMENT,
    NO_ACTIONABLE_COMMITMENT, UNSUPPORTED_FILE_TYPE, ignored_path_reason,
    is_supported_path, rejection,
)
from .relevance import classify_document


def scan_file(
    item: dict[str, object],
    *,
    today: date | None = None,
    month_first: bool = False,
) -> tuple[list[dict[str, object]], int, str | None, list[dict[str, object]]]:
    today = today or date.today()
    path = str(item.get("path") or "untitled")
    if item.get("skip_reason"):
        return [], 0, str(item["skip_reason"]), []
    ignored = ignored_path_reason(path)
    if ignored:
        return [], 0, rejection(ignored, path), []
    if not is_supported_path(path):
        return [], 0, rejection(UNSUPPORTED_FILE_TYPE, path), []
    try:
        content = decode_upload(item)
    except UnreadableFile as exc:
        return [], 0, str(exc), []
    if not content:
        return [], 0, rejection(NO_ACTIONABLE_COMMITMENT, path, "empty file"), []
    document_id = hashlib.sha256(" ".join(content.split()).encode("utf-8")).hexdigest()[:20]
    bank_summary = analyze_bank_statement(content)
    bank_statements: list[dict[str, object]] = []
    if bank_summary:
        bank_statements.append({
            "id": document_id,
            "source": path,
            "title": "Bank Account Statement",
            **bank_summary.as_dict(),
        })
    relevance = classify_document(path, content)
    if not relevance.relevant:
        return [], 0, rejection(IRRELEVANT_DOCUMENT, path), []
    extraction = extract_commitments(
        SourceFile(
            path=path,
            content=content,
            modified=_optional_string(item.get("modified")),
            document_id=document_id,
            domain=relevance.domain,
        ),
        today,
        month_first,
    )
    if not extraction.commitments:
        if bank_statements:
            return [], extraction.candidates, None, bank_statements
        reason = HISTORICAL_NOISE if extraction.rejections.get(HISTORICAL_NOISE) else NO_ACTIONABLE_COMMITMENT
        return [], extraction.candidates, rejection(reason, path), []
    return [commitment.as_dict() for commitment in extraction.commitments], extraction.candidates, None, bank_statements


def scan_files(
    files: list[dict[str, object]],
    *,
    today: date | None = None,
    month_first: bool = False,
) -> dict[str, object]:
    today = today or date.today()
    commitments: list[Commitment] = []
    errors: list[str] = []
    candidates = 0
    scanned = 0
    bank_statements: list[dict[str, object]] = []

    for item in files:
        found, reviewed, warning, analyses = scan_file(item, today=today, month_first=month_first)
        if warning:
            errors.append(warning)
            continue
        scanned += 1
        candidates += reviewed
        commitments.extend(Commitment(**item) for item in found)
        bank_statements.extend(analyses)

    skipped_files = len(errors)
    commitments, duplicate_count = _deduplicate(commitments)
    if duplicate_count:
        errors.append(rejection(DUPLICATE_COMMITMENT, f"{duplicate_count} repeated commitment(s)"))
    commitments.sort(key=lambda item: (item.date, -item.confidence, item.source))
    return {
        "today": today.isoformat(),
        "files_scanned": scanned,
        "files_skipped": skipped_files,
        "dates_reviewed": candidates,
        "noise_removed": max(0, candidates - len(commitments)),
        "commitments": [item.as_dict() for item in commitments],
        "bank_statements": bank_statements,
        "warnings": errors[:12],
    }


def _deduplicate(items: list[Commitment]) -> tuple[list[Commitment], int]:
    best: dict[tuple[str, str, str, str], Commitment] = {}
    for item in items:
        normalized = " ".join(item.title.lower().split())[:80]
        identity = item.document_id or item.source
        key = (item.date, item.category, normalized, identity)
        if key not in best or item.confidence > best[key].confidence:
            best[key] = item
    return list(best.values()), len(items) - len(best)


def _optional_string(value: object) -> str | None:
    return str(value) if value else None
