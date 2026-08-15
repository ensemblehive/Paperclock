from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import PurePath


def make_calendar(commitments: list[dict[str, object]], name: str = "Paperclock") -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Paperclock//Deadline Radar//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape(name)}",
    ]
    for item in commitments:
        raw_date = str(item.get("date", "")).replace("-", "")
        if len(raw_date) != 8 or not raw_date.isdigit():
            continue
        title = str(item.get("title", "Commitment"))
        source = str(item.get("source", ""))
        page = item.get("page")
        event_id = hashlib.sha256(str(item.get("id", raw_date)).encode("utf-8")).hexdigest()[:24]
        location = f"{source}, page {page}" if isinstance(page, int) and page > 0 else source
        base_desc = f"Found in {location}. {item.get('reason', '')}".strip()
        extra_parts = []
        notice_days = item.get("notice_days")
        if isinstance(notice_days, int) and notice_days > 0:
            extra_parts.append(f"Requires {notice_days}-day notice")
        description = f"{base_desc} | {' | '.join(extra_parts)}" if extra_parts else base_desc

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{event_id}@paperclock.local",
                f"DTSTAMP:{now}",
                f"DTSTART;VALUE=DATE:{raw_date}",
                f"SUMMARY:{_escape(title)}",
                f"DESCRIPTION:{_escape(description)}",
                f"CATEGORIES:{_escape(str(item.get('category', 'action')).title())}",
                "TRANSP:TRANSPARENT",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escape(title)} (1 week reminder)",
                "TRIGGER:-P7D",
                "END:VALARM",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escape(title)} (1 day reminder)",
                "TRIGGER:-P1D",
                "END:VALARM",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _escape(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
