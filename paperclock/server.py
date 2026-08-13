from __future__ import annotations

import json
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .calendar import make_calendar
from .scanner import scan_files


MAX_REQUEST_BYTES = 28 * 1024 * 1024


class PaperclockHandler(BaseHTTPRequestHandler):
    server_version = "Paperclock/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self._json({"ok": True, "service": "paperclock"})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            payload = self._payload()
            if self.path == "/api/scan":
                files = payload.get("files", [])
                if not isinstance(files, list):
                    raise ValueError("files must be a list")
                today = _parse_today(payload.get("today"))
                result = scan_files(
                    files,
                    today=today,
                    month_first=payload.get("date_order") == "month-first",
                )
                self._json(result)
            elif self.path == "/api/calendar":
                commitments = payload.get("commitments", [])
                if not isinstance(commitments, list):
                    raise ValueError("commitments must be a list")
                body = make_calendar(commitments).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self._cors_headers()
                self.send_header("Content-Type", "text/calendar; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="paperclock.ics"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            self._json({"error": "Paperclock could not finish that scan."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:
        if self.path != "/api/health":
            super().log_message(format, *args)

    def _payload(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request is empty or larger than 28 MB")
        payload = json.loads(self.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        return payload

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith(("http://localhost:", "http://127.0.0.1:", "https://")):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")


def serve(host: str = "127.0.0.1", port: int = 4312) -> None:
    server = ThreadingHTTPServer((host, port), PaperclockHandler)
    print(f"Paperclock engine ready at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _parse_today(value: object) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(str(value))
