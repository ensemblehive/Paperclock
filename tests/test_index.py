from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sqlite3

from paperclock.index import ScanIndex


class ScanIndexTests(unittest.TestCase):
    def test_legacy_not_null_analysis_column_accepts_uncached_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.sqlite3"
            with sqlite3.connect(path) as database:
                database.executescript(
                    """
                    CREATE TABLE scan_sessions (
                        id TEXT PRIMARY KEY, scan_key TEXT NOT NULL, total INTEGER NOT NULL,
                        date_order TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE scan_files (
                        scan_id TEXT NOT NULL, path TEXT NOT NULL, fingerprint TEXT NOT NULL,
                        cache_key TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT,
                        analysis_json TEXT NOT NULL DEFAULT '[]', candidates INTEGER NOT NULL DEFAULT 0,
                        warning TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (scan_id, path)
                    );
                    CREATE TABLE file_cache (
                        cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL,
                        analysis_json TEXT NOT NULL DEFAULT '[]', candidates INTEGER NOT NULL,
                        warning TEXT, updated_at TEXT NOT NULL
                    );
                    """
                )

            index = ScanIndex(path)
            scan_id, _ = index.start_scan("legacy-folder", 1, "day-first")
            needed = index.register_manifest(scan_id, [{"path": "policy.pdf", "fingerprint": "new-file"}])

            self.assertEqual(needed, ["new-file"])
            self.assertEqual(index.snapshot(scan_id)["files_pending"], 1)

    def test_large_manifest_is_resumable_and_reuses_unchanged_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = ScanIndex(Path(directory) / "index.sqlite3")
            files = [
                {"path": f"folder/file-{number}.txt", "fingerprint": f"fp-{number}"}
                for number in range(1_200)
            ]
            scan_id, resumed = index.start_scan("large-folder", len(files), "day-first")
            self.assertFalse(resumed)
            needed: list[str] = []
            for offset in range(0, len(files), 400):
                needed.extend(index.register_manifest(scan_id, files[offset:offset + 400]))
            self.assertEqual(len(needed), 1_200)

            first = files[0]
            index.store_file(
                scan_id,
                path=str(first["path"]),
                fingerprint=str(first["fingerprint"]),
                commitments=[{"id": "one", "date": "2027-01-01", "title": "Renew plan", "source": first["path"], "confidence": 90}],
                analyses=[{"id": "statement", "source": first["path"], "transaction_count": 2}],
                candidates=2,
                warning=None,
            )
            index.set_status(scan_id, "cancelled")
            resumed_id, resumed = index.start_scan("large-folder", len(files), "day-first")
            self.assertTrue(resumed)
            self.assertEqual(resumed_id, scan_id)
            snapshot = index.snapshot(scan_id)
            self.assertEqual(snapshot["files_processed"], 1)
            self.assertEqual(len(snapshot["commitments"]), 1)
            self.assertEqual(len(snapshot["bank_statements"]), 1)

            index.set_status(scan_id, "complete")
            next_id, resumed = index.start_scan("large-folder", len(files), "day-first")
            self.assertFalse(resumed)
            self.assertNotEqual(next_id, scan_id)
            reused = index.register_manifest(next_id, [first])
            self.assertEqual(reused, [])
            self.assertEqual(index.snapshot(next_id)["files_cached"], 1)
            self.assertEqual(len(index.snapshot(next_id)["bank_statements"]), 1)

    def test_index_query_uses_status_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            index = ScanIndex(Path(directory) / "index.sqlite3")
            with index._connect() as database:
                plan = database.execute(
                    "EXPLAIN QUERY PLAN SELECT fingerprint FROM scan_files WHERE scan_id = ? AND status = 'pending'",
                    ("scan",),
                ).fetchall()
            self.assertTrue(any("idx_scan_files_status" in str(row[3]) for row in plan))


if __name__ == "__main__":
    unittest.main()
