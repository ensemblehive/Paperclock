from __future__ import annotations

from datetime import date

from .extractor import extract_commitments
from .models import Commitment, SourceFile
from .readers import UnreadableFile, decode_upload


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

    for item in files[:500]:
        path = str(item.get("path") or "untitled")
        try:
            content = decode_upload(item)
        except UnreadableFile as exc:
            errors.append(str(exc))
            continue
        if not content:
            continue
        scanned += 1
        extraction = extract_commitments(
            SourceFile(path=path, content=content, modified=_optional_string(item.get("modified"))),
            today,
            month_first,
        )
        candidates += extraction.candidates
        commitments.extend(extraction.commitments)

    commitments = _deduplicate(commitments)
    commitments.sort(key=lambda item: (item.date, -item.confidence, item.source))
    return {
        "today": today.isoformat(),
        "files_scanned": scanned,
        "files_skipped": len(errors),
        "dates_reviewed": candidates,
        "noise_removed": max(0, candidates - len(commitments)),
        "commitments": [item.as_dict() for item in commitments],
        "warnings": errors[:12],
    }


def _deduplicate(items: list[Commitment]) -> list[Commitment]:
    best: dict[tuple[str, str, str], Commitment] = {}
    for item in items:
        normalized = " ".join(item.title.lower().split())[:80]
        key = (item.date, item.source, normalized)
        if key not in best or item.confidence > best[key].confidence:
            best[key] = item
    return list(best.values())


def _optional_string(value: object) -> str | None:
    return str(value) if value else None
