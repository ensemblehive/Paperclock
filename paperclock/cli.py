from __future__ import annotations

import argparse
import base64
import json
from datetime import datetime
from pathlib import Path

from .calendar import make_calendar
from .readers import DOCUMENT_EXTENSIONS, SUPPORTED_EXTENSIONS
from .scanner import scan_files
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paperclock", description="Find actionable dates hiding in files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="scan a file or folder")
    scan_parser.add_argument("path", type=Path)
    scan_parser.add_argument("--month-first", action="store_true", help="read 04/07/2027 as April 7")
    scan_parser.add_argument("--calendar", type=Path, help="also write an .ics calendar")
    scan_parser.add_argument("--json", action="store_true", help="print machine-readable output")

    serve_parser = subparsers.add_parser("serve", help="run the local analysis engine")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=4312, type=int)

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.host, args.port)
        return 0

    files = _load_path(args.path)
    result = scan_files(files, month_first=args.month_first)
    if args.calendar:
        args.calendar.write_text(make_calendar(result["commitments"]), encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_report(result)
    return 0


def _load_path(path: Path) -> list[dict[str, object]]:
    candidates = [path] if path.is_file() else sorted(path.rglob("*"))
    files: list[dict[str, object]] = []
    for candidate in candidates:
        if not candidate.is_file() or candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        raw = candidate.read_bytes()
        relative = str(candidate if path.is_file() else candidate.relative_to(path))
        if candidate.suffix.lower() in DOCUMENT_EXTENSIONS:
            content = base64.b64encode(raw).decode("ascii")
            encoding = "base64"
        else:
            content = raw.decode("utf-8", errors="replace")
            encoding = "text"
        files.append(
            {
                "path": relative,
                "content": content,
                "encoding": encoding,
                "modified": datetime.fromtimestamp(candidate.stat().st_mtime).isoformat(),
            }
        )
    return files


def _print_report(result: dict[str, object]) -> None:
    commitments = result["commitments"]
    print(f"Paperclock found {len(commitments)} commitments in {result['files_scanned']} files.\n")
    for item in commitments:
        print(f"{item['date']}  {item['title']}")
        print(f"            {item['source']} · {item['category']} · {item['confidence']}%\n")


if __name__ == "__main__":
    raise SystemExit(main())
