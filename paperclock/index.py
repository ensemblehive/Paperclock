from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


ENGINE_VERSION = "0.3"


class ScanIndex:
    """Small durable index for resumable scans and unchanged-file reuse."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(".paperclock/index.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._initialize()

    def start_scan(self, scan_key: str, total: int, date_order: str) -> tuple[str, bool]:
        with self._write_lock, self._connect() as database:
            row = database.execute(
                """
                SELECT id FROM scan_sessions
                WHERE scan_key = ? AND date_order = ? AND status IN ('running', 'cancelled')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (scan_key, date_order),
            ).fetchone()
            if row:
                database.execute(
                    "UPDATE scan_sessions SET status = 'running', total = ?, updated_at = ? WHERE id = ?",
                    (total, _now(), row["id"]),
                )
                return str(row["id"]), True

            scan_id = uuid.uuid4().hex
            now = _now()
            database.execute(
                """
                INSERT INTO scan_sessions (id, scan_key, total, date_order, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (scan_id, scan_key, total, date_order, now, now),
            )
            return scan_id, False

    def register_manifest(self, scan_id: str, files: list[dict[str, object]]) -> list[str]:
        needed: list[str] = []
        with self._write_lock, self._connect() as database:
            session = self._session(database, scan_id)
            date_order = str(session["date_order"])
            for item in files:
                path = str(item.get("path") or "untitled")
                fingerprint = str(item.get("fingerprint") or "")
                if not fingerprint:
                    continue
                cache_key = _cache_key(path, fingerprint, date_order)
                cached = database.execute(
                    "SELECT result_json, candidates, warning FROM file_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                status = "cached" if cached else "pending"
                database.execute(
                    """
                    INSERT INTO scan_files
                        (scan_id, path, fingerprint, cache_key, status, result_json, candidates, warning, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_id, path) DO UPDATE SET
                        fingerprint = excluded.fingerprint,
                        cache_key = excluded.cache_key,
                        status = CASE
                            WHEN scan_files.status IN ('done', 'cached') AND scan_files.cache_key = excluded.cache_key
                            THEN scan_files.status ELSE excluded.status END,
                        result_json = CASE
                            WHEN scan_files.status IN ('done', 'cached') AND scan_files.cache_key = excluded.cache_key
                            THEN scan_files.result_json ELSE excluded.result_json END,
                        candidates = CASE
                            WHEN scan_files.status IN ('done', 'cached') AND scan_files.cache_key = excluded.cache_key
                            THEN scan_files.candidates ELSE excluded.candidates END,
                        warning = CASE
                            WHEN scan_files.status IN ('done', 'cached') AND scan_files.cache_key = excluded.cache_key
                            THEN scan_files.warning ELSE excluded.warning END,
                        updated_at = excluded.updated_at
                    """,
                    (
                        scan_id,
                        path,
                        fingerprint,
                        cache_key,
                        status,
                        cached["result_json"] if cached else None,
                        cached["candidates"] if cached else 0,
                        cached["warning"] if cached else None,
                        _now(),
                    ),
                )
                current = database.execute(
                    "SELECT status FROM scan_files WHERE scan_id = ? AND path = ?",
                    (scan_id, path),
                ).fetchone()
                if current and current["status"] == "pending":
                    needed.append(fingerprint)
            database.execute(
                "UPDATE scan_sessions SET updated_at = ? WHERE id = ?",
                (_now(), scan_id),
            )
        return needed

    def store_file(
        self,
        scan_id: str,
        *,
        path: str,
        fingerprint: str,
        commitments: list[dict[str, object]],
        candidates: int,
        warning: str | None,
    ) -> None:
        result_json = json.dumps(commitments, separators=(",", ":"))
        status = "skipped" if warning else "done"
        with self._write_lock, self._connect() as database:
            session = self._session(database, scan_id)
            cache_key = _cache_key(path, fingerprint, str(session["date_order"]))
            if not warning:
                database.execute(
                    """
                    INSERT INTO file_cache (cache_key, result_json, candidates, warning, updated_at)
                    VALUES (?, ?, ?, NULL, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        result_json = excluded.result_json,
                        candidates = excluded.candidates,
                        warning = NULL,
                        updated_at = excluded.updated_at
                    """,
                    (cache_key, result_json, candidates, _now()),
                )
            database.execute(
                """
                UPDATE scan_files
                SET status = ?, result_json = ?, candidates = ?, warning = ?, updated_at = ?
                WHERE scan_id = ? AND path = ? AND fingerprint = ?
                """,
                (status, result_json, candidates, warning, _now(), scan_id, path, fingerprint),
            )
            database.execute(
                "UPDATE scan_sessions SET updated_at = ? WHERE id = ?",
                (_now(), scan_id),
            )

    def set_status(self, scan_id: str, status: str) -> None:
        with self._write_lock, self._connect() as database:
            self._session(database, scan_id)
            database.execute(
                "UPDATE scan_sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), scan_id),
            )

    def snapshot(self, scan_id: str) -> dict[str, object]:
        with self._connect() as database:
            session = self._session(database, scan_id)
            rows = database.execute(
                """
                SELECT path, status, result_json, candidates, warning
                FROM scan_files WHERE scan_id = ? ORDER BY updated_at, path
                """,
                (scan_id,),
            ).fetchall()

        commitments: list[dict[str, object]] = []
        warnings: list[str] = []
        candidates = 0
        counts = {"processed": 0, "cached": 0, "skipped": 0, "pending": 0}
        for row in rows:
            status = str(row["status"])
            if status in {"done", "cached", "skipped"}:
                counts["processed"] += 1
            if status == "cached":
                counts["cached"] += 1
            if status == "skipped":
                counts["skipped"] += 1
            if status == "pending":
                counts["pending"] += 1
            candidates += int(row["candidates"] or 0)
            if row["result_json"]:
                commitments.extend(json.loads(row["result_json"]))
            if row["warning"]:
                warnings.append(str(row["warning"]))

        commitments = _deduplicate(commitments)
        commitments.sort(key=lambda item: (str(item.get("date", "")), -int(item.get("confidence", 0))))
        elapsed = max(0.001, (_parse_time(str(session["updated_at"])) - _parse_time(str(session["created_at"]))).total_seconds())
        rate = counts["processed"] / elapsed
        remaining = max(0, int(session["total"]) - counts["processed"])
        eta = round(remaining / rate) if rate > 0 and counts["processed"] else None
        return {
            "scan_id": scan_id,
            "status": session["status"],
            "today": datetime.now().date().isoformat(),
            "total": int(session["total"]),
            "files_scanned": counts["processed"] - counts["skipped"],
            "files_processed": counts["processed"],
            "files_cached": counts["cached"],
            "files_skipped": counts["skipped"],
            "files_pending": counts["pending"],
            "dates_reviewed": candidates,
            "noise_removed": max(0, candidates - len(commitments)),
            "commitments": commitments,
            "warnings": warnings[:24],
            "rate": round(rate, 1),
            "eta_seconds": eta,
        }

    def pending_fingerprints(self, scan_id: str) -> set[str]:
        with self._connect() as database:
            self._session(database, scan_id)
            rows = database.execute(
                "SELECT fingerprint FROM scan_files WHERE scan_id = ? AND status = 'pending'",
                (scan_id,),
            ).fetchall()
        return {str(row["fingerprint"]) for row in rows}

    def _initialize(self) -> None:
        with self._connect() as database:
            database.executescript(
                """
                CREATE TABLE IF NOT EXISTS scan_sessions (
                    id TEXT PRIMARY KEY,
                    scan_key TEXT NOT NULL,
                    total INTEGER NOT NULL,
                    date_order TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scan_files (
                    scan_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    candidates INTEGER NOT NULL DEFAULT 0,
                    warning TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scan_id, path),
                    FOREIGN KEY (scan_id) REFERENCES scan_sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS file_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    candidates INTEGER NOT NULL,
                    warning TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scan_sessions_resume
                    ON scan_sessions(scan_key, date_order, updated_at);
                CREATE INDEX IF NOT EXISTS idx_scan_files_status
                    ON scan_files(scan_id, status);
                """
            )
            database.execute("PRAGMA optimize")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.path, timeout=20)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA journal_mode = WAL")
        database.execute("PRAGMA foreign_keys = ON")
        try:
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

    @staticmethod
    def _session(database: sqlite3.Connection, scan_id: str) -> sqlite3.Row:
        row = database.execute("SELECT * FROM scan_sessions WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            raise ValueError("unknown scan")
        return row


def _cache_key(path: str, fingerprint: str, date_order: str) -> str:
    value = f"{ENGINE_VERSION}\0{date_order}\0{path}\0{fingerprint}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _deduplicate(items: list[dict[str, object]]) -> list[dict[str, object]]:
    best: dict[tuple[str, str, str], dict[str, object]] = {}
    for item in items:
        title = " ".join(str(item.get("title", "")).lower().split())[:80]
        key = (str(item.get("date", "")), str(item.get("source", "")), title)
        if key not in best or int(item.get("confidence", 0)) > int(best[key].get("confidence", 0)):
            best[key] = item
    return list(best.values())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)
