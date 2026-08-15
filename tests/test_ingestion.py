from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from paperclock.cli import _load_path
from paperclock.extractor import extract_commitments
from paperclock.models import SourceFile
from paperclock.policy import ignored_path_reason, is_supported_path
from paperclock.scanner import scan_file, scan_files


TODAY = date(2026, 8, 13)


class IngestionPolicyTests(unittest.TestCase):
    def test_allowlist_is_strict(self) -> None:
        for name in ("policy.pdf", "contract.docx", "letter.pages", "notice.eml", "mail.msg", "statement.csv", "ledger.xlsx", "receipt.png", "invoice.jpg", "card.webp"):
            self.assertTrue(is_supported_path(name), name)
        for name in ("logos.rst.txt", "notes.md", "archive.zip", "config.json", "app.py", "binary.exe"):
            self.assertFalse(is_supported_path(name), name)

    def test_ignored_paths_are_recognized(self) -> None:
        self.assertEqual(ignored_path_reason("venv/lib/python/docs/logos.rst.txt"), "ignored_directory")
        self.assertEqual(ignored_path_reason("node_modules/package/notice.pdf"), "ignored_directory")
        self.assertEqual(ignored_path_reason(".git/objects/report.pdf"), "ignored_directory")
        self.assertEqual(ignored_path_reason("contracts/.draft.pdf"), "ignored_directory")

    def test_folder_walk_prunes_ignored_trees_and_unsupported_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "venv" / "lib" / "python" / "docs").mkdir(parents=True)
            (root / "venv" / "lib" / "python" / "docs" / "logos.rst.txt").write_text("dated noise")
            (root / "node_modules" / "package").mkdir(parents=True)
            (root / "node_modules" / "package" / "notice.pdf").write_bytes(b"not visited")
            (root / "notes.md").write_text("Renew 2027-01-01")
            (root / "policy.eml").write_text("Subject: Policy renewal\n\nPolicy renews on 2027-01-01")

            loaded = _load_path(root)

        self.assertEqual([item["path"] for item in loaded], ["policy.eml"])

    def test_unsupported_file_never_reaches_reader(self) -> None:
        rejected = (
            "venv/lib/python/docs/logos.rst.txt", ".git/objects/report.pdf",
            "node_modules/pkg/notice.pdf", "site-packages/docs/manual.pdf", "README.md",
            "CHANGELOG.rst", "documentation.html", "script.py", "package.json",
        )
        with patch("paperclock.scanner.decode_upload") as decode:
            outcomes = [scan_file({"path": path, "content": "expires 2028-01-01"}, today=TODAY) for path in rejected]
        decode.assert_not_called()
        self.assertTrue(all(found == [] and reviewed == 0 and warning and analyses == [] for found, reviewed, warning, analyses in outcomes))
        self.assertTrue(str(outcomes[0][2]).startswith("ignored_directory:"))
        self.assertTrue(str(outcomes[-1][2]).startswith("unsupported_file_type:"))

    def test_irrelevant_document_is_rejected_before_date_extraction(self) -> None:
        with patch("paperclock.scanner.decode_upload", return_value="Python 3.12 was released October 2, 2023."):
            found, reviewed, warning, analyses = scan_file({"path": "release.pdf"}, today=TODAY)
        self.assertEqual((found, reviewed), ([], 0))
        self.assertEqual(analyses, [])
        self.assertEqual(warning, "irrelevant_document: release.pdf")

    def test_supported_documents_with_real_commitments_proceed(self) -> None:
        samples = {
            "Health_Insurance_Policy.pdf": "Health insurance policy coverage expires on 2028-02-01.",
            "Electricity_Bill.pdf": "Electricity bill payment is due by 2026-08-31.",
            "Lease_Agreement.docx": "The lease agreement must be renewed by 2027-09-15.",
            "Passport_Renewal.pdf": "Passport expires on 2027-06-12 and must be renewed.",
            "Invoice.eml": "Subject: Electricity invoice\n\nInvoice payment is due by 2026-08-31.",
        }
        for path, content in samples.items():
            with self.subTest(path=path), patch("paperclock.scanner.decode_upload", return_value=content):
                found, reviewed, warning, analyses = scan_file({"path": path}, today=TODAY)
                self.assertIsNone(warning)
                self.assertEqual(analyses, [])
                self.assertEqual(reviewed, 1)
                self.assertEqual(len(found), 1)

    def test_historical_metadata_is_noise_but_future_expiry_survives(self) -> None:
        source = SourceFile(
            "policy.pdf",
            "Policy start date: 2018-01-01. Health insurance coverage expires on 2028-01-01.",
            domain="insurance",
        )
        result = extract_commitments(source, TODAY)
        self.assertEqual([item.date for item in result.commitments], ["2028-01-01"])
        self.assertGreaterEqual(result.rejections.get("historical_noise", 0), 1)

    def test_actionable_dates_survive_temporal_validation(self) -> None:
        cases = (
            ("Health insurance policy expires 16/09/2026.", "2026-09-16"),
            ("Electricity bill payment due 31/08/2026.", "2026-08-31"),
            ("Laptop warranty expires 04/02/2027.", "2027-02-04"),
        )
        for content, expected in cases:
            with self.subTest(content=content):
                result = extract_commitments(SourceFile("document.pdf", content), TODAY)
                self.assertEqual([item.date for item in result.commitments], [expected])

    def test_printed_and_generated_dates_are_not_commitments(self) -> None:
        source = SourceFile(
            "bill.pdf",
            "Electricity bill printed on 2026-09-01. Invoice generated on 2026-09-02.",
            domain="billing",
        )
        result = extract_commitments(source, TODAY)
        self.assertEqual(result.commitments, [])
        self.assertEqual(result.rejections.get("historical_noise"), 2)

        old = extract_commitments(
            SourceFile(
                "policy.pdf",
                "Insurance policy copyright 2001-01-01. Software release date: 2010-03-12.",
                domain="insurance",
            ),
            TODAY,
        )
        self.assertEqual(old.commitments, [])

    def test_duplicate_documents_collapse_across_locations(self) -> None:
        content = "Subject: Warranty\n\nCamera warranty coverage expires on 2027-04-01."
        result = scan_files(
            [
                {"path": "Receipts/warranty.eml", "content": content, "encoding": "text"},
                {"path": "Backups/warranty-copy.eml", "content": content, "encoding": "text"},
            ],
            today=TODAY,
        )
        self.assertEqual(len(result["commitments"]), 1)
        self.assertTrue(any(str(item).startswith("duplicate_commitment:") for item in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
