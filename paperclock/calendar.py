from __future__ import annotations

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
        event_id = str(item.get("id", raw_date))
        location = f"{source}, page {page}" if isinstance(page, int) and page > 0 else source
        description = f"Found in {location}. {item.get('reason', '')}".strip()
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
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
