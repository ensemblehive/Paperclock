from __future__ import annotations

import unittest
from datetime import date

from paperclock.models import SourceFile
from paperclock.extractor import extract_commitments
from paperclock.scanner import scan_files


class ExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 8, 13)

    def test_keeps_commitments_and_drops_historical_dates(self) -> None:
        text = """Statement date: 2026-08-01.
        Your plan renews on September 14, 2026.
        Coverage is valid until 2026-10-30.
        The order was completed on July 4, 2026.
        """
        result = extract_commitments(SourceFile("policy.txt", text), self.today)
        self.assertEqual([item.date for item in result.commitments], ["2026-09-14", "2026-10-30"])
        self.assertEqual(result.candidates, 4)

    def test_numeric_order_is_explicit_and_flagged(self) -> None:
        source = SourceFile("notice.txt", "Cancel by 04/07/2027.")
        day_first = extract_commitments(source, self.today).commitments[0]
        month_first = extract_commitments(source, self.today, month_first=True).commitments[0]
        self.assertEqual(day_first.date, "2027-07-04")
        self.assertEqual(month_first.date, "2027-04-07")
        self.assertTrue(day_first.ambiguous)

    def test_relative_business_days_use_file_date(self) -> None:
        source = SourceFile(
            "letter.txt",
            "You must respond within 5 business days.",
            "2026-08-14T09:30:00",
        )
        commitment = extract_commitments(source, self.today).commitments[0]
        self.assertEqual(commitment.date, "2026-08-21")

    def test_titles_are_short_and_domain_specific(self) -> None:
        source = SourceFile(
            "telecom/account-notice.txt",
            "Your mobile number will be valid till 31.12.2026, kindly make payment before expiry.",
        )
        commitment = extract_commitments(source, self.today).commitments[0]
        self.assertEqual(commitment.title, "SIM Plan Expiry")
        self.assertIn("mobile number will be valid", commitment.snippet)

    def test_pdf_page_markers_are_preserved_on_commitments(self) -> None:
        source = SourceFile(
            "policies/health-insurance.pdf",
            "[[PAPERCLOCK_PAGE:3]]\nYour health insurance renews on September 14, 2026.",
        )
        commitment = extract_commitments(source, self.today).commitments[0]
        self.assertEqual(commitment.title, "Health Insurance Renewal")
        self.assertEqual(commitment.page, 3)

    def test_scan_reports_noise_and_skipped_files(self) -> None:
        result = scan_files(
            [
                {"path": "a.txt", "content": "Renew before 2026-09-01.", "encoding": "text"},
                {"path": "photo.jpg", "content": "", "encoding": "text"},
            ],
            today=self.today,
        )
        self.assertEqual(result["files_scanned"], 1)
        self.assertEqual(result["files_skipped"], 1)
        self.assertEqual(len(result["commitments"]), 1)


if __name__ == "__main__":
    unittest.main()
