from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .calendar import make_calendar
from .index import ScanIndex
from .scanner import scan_file, scan_files


MAX_REQUEST_BYTES = 40 * 1024 * 1024
WORKERS = max(2, min(6, (os.cpu_count() or 2)))
INDEX = ScanIndex()
EXECUTOR = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="paperclock")
CANCELLED: set[str] = set()
CANCEL_LOCK = threading.Lock()


class PaperclockHandler(BaseHTTPRequestHandler):
    server_version = "Paperclock/0.2"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json({"ok": True, "service": "paperclock", "version": "0.2", "workers": WORKERS})
        elif path.startswith("/api/scans/"):
            scan_id = path.removeprefix("/api/scans/").split("/", 1)[0]
            self._json(INDEX.snapshot(scan_id))
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            payload = self._payload()
            if path == "/api/scan":
                self._legacy_scan(payload)
            elif path == "/api/scans/start":
                self._start_scan(payload)
            elif path.endswith("/manifest") and path.startswith("/api/scans/"):
                self._manifest(path, payload)
            elif path.endswith("/batch") and path.startswith("/api/scans/"):
                self._batch(path, payload)
            elif path.endswith("/cancel") and path.startswith("/api/scans/"):
                self._cancel(path)
            elif path.endswith("/complete") and path.startswith("/api/scans/"):
                self._complete(path)
            elif path == "/api/calendar":
                self._calendar(payload)
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            print(f"Paperclock error: {exc}", flush=True)
            self._json({"error": "Paperclock could not finish that request."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _start_scan(self, payload: dict[str, object]) -> None:
        total = int(payload.get("total", 0))
        if total < 1:
            raise ValueError("scan must contain at least one supported file")
        scan_key = str(payload.get("scan_key") or "anonymous")[:256]
        date_order = "month-first" if payload.get("date_order") == "month-first" else "day-first"
        scan_id, resumed = INDEX.start_scan(scan_key, total, date_order)
        with CANCEL_LOCK:
            CANCELLED.discard(scan_id)
        snapshot = INDEX.snapshot(scan_id)
        snapshot["resumed"] = resumed
        self._json(snapshot, HTTPStatus.CREATED)

    def _manifest(self, path: str, payload: dict[str, object]) -> None:
        scan_id = _scan_id(path)
        files = payload.get("files", [])
        if not isinstance(files, list) or len(files) > 500:
            raise ValueError("manifest batches must contain at most 500 files")
        needed = INDEX.register_manifest(scan_id, files)
        self._json({"needed": needed, "progress": INDEX.snapshot(scan_id)})

    def _batch(self, path: str, payload: dict[str, object]) -> None:
        scan_id = _scan_id(path)
        files = payload.get("files", [])
        if not isinstance(files, list) or len(files) > 12:
            raise ValueError("upload batches must contain at most 12 files")
        pending = INDEX.pending_fingerprints(scan_id)
        snapshot = INDEX.snapshot(scan_id)
        month_first = payload.get("date_order") == "month-first"
        today = _parse_today(payload.get("today"))

        futures = {
            EXECUTOR.submit(scan_file, item, today=today, month_first=month_first): item
            for item in files
            if str(item.get("fingerprint", "")) in pending
        }
        done, unfinished = wait(futures, timeout=60)
        for future in done:
            item = futures[future]
            if _is_cancelled(scan_id):
                break
            path_value = str(item.get("path") or "untitled")
            fingerprint = str(item.get("fingerprint") or "")
            try:
                commitments, candidates, warning = future.result()
            except Exception:
                commitments, candidates, warning = [], 0, f"{path_value}: reader timed out or failed"
            INDEX.store_file(
                scan_id,
                path=path_value,
                fingerprint=fingerprint,
                commitments=commitments,
                candidates=candidates,
                warning=warning,
            )
        for future in unfinished:
            item = futures[future]
            future.cancel()
            path_value = str(item.get("path") or "untitled")
            INDEX.store_file(
                scan_id,
                path=path_value,
                fingerprint=str(item.get("fingerprint") or ""),
                commitments=[],
                candidates=0,
                warning=f"{path_value}: reader exceeded the 60 second safety limit",
            )
        self._json(INDEX.snapshot(scan_id))

    def _cancel(self, path: str) -> None:
        scan_id = _scan_id(path)
        with CANCEL_LOCK:
            CANCELLED.add(scan_id)
        INDEX.set_status(scan_id, "cancelled")
        self._json(INDEX.snapshot(scan_id))

    def _complete(self, path: str) -> None:
        scan_id = _scan_id(path)
        INDEX.set_status(scan_id, "complete")
        self._json(INDEX.snapshot(scan_id))

    def _legacy_scan(self, payload: dict[str, object]) -> None:
        files = payload.get("files", [])
        if not isinstance(files, list):
            raise ValueError("files must be a list")
        self._json(
            scan_files(
                files,
                today=_parse_today(payload.get("today")),
                month_first=payload.get("date_order") == "month-first",
            )
        )

    def _calendar(self, payload: dict[str, object]) -> None:
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

    def log_message(self, format: str, *args: Any) -> None:
        if self.path != "/api/health":
            super().log_message(format, *args)

    def _payload(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request is empty or larger than 40 MB")
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
    print(f"Paperclock engine ready at http://{host}:{port} with {WORKERS} readers", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        EXECUTOR.shutdown(wait=False, cancel_futures=True)
        server.server_close()


def _scan_id(path: str) -> str:
    parts = path.strip("/").split("/")
    if len(parts) < 4:
        raise ValueError("invalid scan path")
    return parts[2]


def _is_cancelled(scan_id: str) -> bool:
    with CANCEL_LOCK:
        return scan_id in CANCELLED


def _parse_today(value: object) -> date:
    if not value:
        return date.today()
    return date.fromisoformat(str(value))
