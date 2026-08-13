from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paperclock.index import ScanIndex


class ScanIndexTests(unittest.TestCase):
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

            index.set_status(scan_id, "complete")
            next_id, resumed = index.start_scan("large-folder", len(files), "day-first")
            self.assertFalse(resumed)
            self.assertNotEqual(next_id, scan_id)
            reused = index.register_manifest(next_id, [first])
            self.assertEqual(reused, [])
            self.assertEqual(index.snapshot(next_id)["files_cached"], 1)

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
