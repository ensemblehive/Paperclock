from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

from paperclock.extractor import extract_commitments
from paperclock.models import SourceFile
from paperclock.calendar import make_calendar
from paperclock.index import ScanIndex
from paperclock.readers import _read_docx, _safe_read_zip_entry, UnreadableFile
from paperclock.server import _is_allowed_host, _is_allowed_origin


class FeatureAndSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 8, 15)

    def test_deadline_amount_and_reference_traces_are_not_extracted(self) -> None:
        text = "Your car insurance premium of $185.50/mo is due by 2026-10-15. Ref: POL-88219."
        source = SourceFile("Policies/auto.txt", text)
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        item = result.commitments[0]
        self.assertNotIn("amount", item.as_dict())
        self.assertNotIn("currency", item.as_dict())
        self.assertEqual(item.periodicity, "monthly")
        self.assertNotIn("ref_number", item.as_dict())
        self.assertNotIn("185.50", str(item.summary))
        self.assertNotIn("POL-88219", str(item.summary))

    def test_notice_window_arithmetic(self) -> None:
        text = "Your annual software subscription renews on 2026-11-30. Cancellation requires at least 30 days notice prior to renewal."
        source = SourceFile("Contracts/software.txt", text)
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        item = result.commitments[0]
        self.assertEqual(item.notice_days, 30)
        self.assertEqual(item.action_date, "2026-10-31")
        self.assertIn("Action by Oct 31", str(item.summary))

    def test_cors_origin_hardening(self) -> None:
        self.assertTrue(_is_allowed_origin("http://localhost:3000"))
        self.assertTrue(_is_allowed_origin("http://127.0.0.1:3000"))
        self.assertTrue(_is_allowed_origin("http://localhost:4312"))
        self.assertTrue(_is_allowed_origin("http://127.0.0.1:5173"))
        # Security threats: malicious external sites must be blocked
        self.assertFalse(_is_allowed_origin("https://malicious-site.com"))
        self.assertFalse(_is_allowed_origin("http://attacker.com"))
        self.assertFalse(_is_allowed_origin("https://localhost:3000"))
        self.assertFalse(_is_allowed_origin(""))

    def test_host_header_hardening(self) -> None:
        self.assertTrue(_is_allowed_host("localhost:4312"))
        self.assertTrue(_is_allowed_host("127.0.0.1:4312"))
        self.assertTrue(_is_allowed_host("[::1]:4312"))
        self.assertFalse(_is_allowed_host("attacker.example:4312"))
        self.assertFalse(_is_allowed_host("localhost.attacker.example:4312"))
        self.assertFalse(_is_allowed_host("localhost@attacker.example:4312"))

    def test_office_xml_entities_are_rejected(self) -> None:
        xml = b'''<?xml version="1.0"?>
        <!DOCTYPE document [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:p><w:t>&secret;</w:t></w:p>
        </w:document>'''
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", xml)
        with self.assertRaises(UnreadableFile):
            _read_docx("hostile.docx", payload.getvalue())

    def test_suspicious_zip_compression_ratio_is_rejected(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", b"A" * (2 * 1024 * 1024))
        with zipfile.ZipFile(io.BytesIO(payload.getvalue())) as archive:
            with self.assertRaises(UnreadableFile):
                _safe_read_zip_entry(archive, "word/document.xml")

    @unittest.skipUnless(os.name == "posix", "POSIX permission test")
    def test_local_index_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "index.sqlite3")
            ScanIndex(Path(path))
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_calendar_values_cannot_inject_new_properties(self) -> None:
        output = make_calendar([{
            "id": "safe\r\nATTENDEE:mailto:attacker@example.com",
            "date": "2026-09-10",
            "title": "Renew\r\nATTENDEE:mailto:attacker@example.com",
        }])
        self.assertNotIn("\r\nATTENDEE:", output)

    def test_rupee_currency_and_indian_tax_extraction(self) -> None:
        text = "Income Tax Return (ITR-V) for Assessment Year 2026-27. Total tax payable of ₹ 45,500.00 is due by 31-07-2026. Ref: PAN-ABCDE1234F."
        source = SourceFile("Taxes/itr_ack.txt", text, domain="taxes")
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        item = result.commitments[0]
        self.assertNotIn("amount", item.as_dict())
        self.assertNotIn("currency", item.as_dict())
        self.assertEqual(item.date, "2026-07-31")
        self.assertIn("Tax", item.title)
        self.assertNotIn("ref_number", item.as_dict())

    def test_bank_statement_and_fd_maturity(self) -> None:
        text = "HDFC Bank Fixed Deposit Advice. Your FD maturity date is 2026-11-25 for total maturity proceeds of Rs. 1,50,000.00. Account: HDFC-982104."
        source = SourceFile("Bank/fd_advice.txt", text, domain="banking")
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        item = result.commitments[0]
        self.assertNotIn("amount", item.as_dict())
        self.assertNotIn("currency", item.as_dict())
        self.assertEqual(item.date, "2026-11-25")
        self.assertIn("Bank", item.title)

    def test_salary_payslip_extraction(self) -> None:
        text = "Salary Slip for August 2026. Net pay of ₹ 85,000.00 will be credited on 2026-08-31. Ref: EMP-4091."
        source = SourceFile("Payroll/payslip_aug2026.txt", text, domain="employment")
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        item = result.commitments[0]
        self.assertNotIn("amount", item.as_dict())
        self.assertNotIn("currency", item.as_dict())
        self.assertEqual(item.date, "2026-08-31")
        self.assertIn("Salary Slip", item.title)


    def test_programming_book_is_not_classified_as_tax_or_insurance(self) -> None:
        from paperclock.relevance import classify_document
        path = "Personal/Python Programming Essentials for Beginners.docx"
        text = """Python has emerged as one of the most versatile programming languages in the world today.
Its simplicity and readability make it an ideal choice for beginners, while also being powerful enough for experienced developers.
Python is used across various domains, from web development to data science, artificial intelligence, and automation.
Published on October 16, 2025.
In this book, you will learn basic syntax, object-oriented principles, and best practices.
"""
        rel = classify_document(path, text)
        self.assertFalse(rel.relevant)
        self.assertIsNone(rel.domain)

        # Ensure extractor produces zero commitments
        source = SourceFile(path, text, domain=rel.domain)
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 0)

    def test_programming_book_examples_never_become_personal_deadlines(self) -> None:
        from paperclock.relevance import classify_document
        text = """Python Programming Essentials for Beginners
        Chapter 8: Working with dates
        Learning objectives and practice exercise.
        Example: A life insurance claim submission deadline is October 16, 2025.
        For instance, a tax return may be due on October 16, 2025.
        Sample code: print(\"renew policy by 2027-09-01\")
        """
        relevance = classify_document("Books/Python Programming Essentials For Beginners.docx", text)
        self.assertFalse(relevance.relevant)
        result = extract_commitments(SourceFile("Books/Python Programming Essentials For Beginners.docx", text), self.today)
        self.assertEqual(result.commitments, [])

    def test_old_date_requires_explicit_unresolved_language(self) -> None:
        ordinary = extract_commitments(
            SourceFile("policy.txt", "Health insurance claim was due on 2025-10-16.", domain="insurance"),
            self.today,
        )
        overdue = extract_commitments(
            SourceFile("policy.txt", "Health insurance payment remains unpaid and overdue since 2025-10-16.", domain="insurance"),
            self.today,
        )
        self.assertEqual(ordinary.commitments, [])
        self.assertEqual(len(overdue.commitments), 1)

    def test_multiline_insurance_schedule_is_recognized(self) -> None:
        from paperclock.relevance import classify_document
        text = """SecureLife General Insurance
        Policy Schedule
        Policy Number: SL-882109
        Name of Insured: A Customer
        Sum Insured: INR 500000
        Period of Insurance
        01/09/2026 to 31/08/2027
        Renewal Premium Due Date
        31/08/2027
        """
        relevance = classify_document("Policies/SecureLife_policy_schedule.pdf", text)
        self.assertTrue(relevance.relevant)
        self.assertEqual(relevance.domain, "insurance")
        result = extract_commitments(SourceFile("Policies/SecureLife_policy_schedule.pdf", text, domain=relevance.domain), self.today)
        self.assertIn("2027-08-31", [item.date for item in result.commitments])

    def test_split_field_labels_work_for_other_real_documents(self) -> None:
        cases = (
            ("Lease_Agreement.pdf", "Lease Agreement\nLandlord: Example Estates\nLease Term\nExpiration Date\n30/09/2027", "housing"),
            ("Laptop_Warranty.pdf", "Warranty Certificate\nWarranty Period: 24 months\nCoverage End Date\n2027-10-14", "warranty"),
            ("Vehicle_Registration.pdf", "Registration Certificate\nVehicle Registration\nRegistration Expiry\n2028-02-20", "vehicle"),
        )
        from paperclock.relevance import classify_document
        for path, text, domain in cases:
            with self.subTest(path=path):
                relevance = classify_document(path, text)
                self.assertTrue(relevance.relevant)
                self.assertEqual(relevance.domain, domain)
                result = extract_commitments(SourceFile(path, text, domain=domain), self.today)
                self.assertGreaterEqual(len(result.commitments), 1)


    def test_image_ocr_extraction(self) -> None:
        from PIL import Image, ImageDraw
        from paperclock.readers import extract_text

        img = Image.new("RGB", (600, 150), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((20, 50), "Health Insurance Renewal Premium Due Date: 2027-04-20", fill="black")
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        text = extract_text("Receipts/health_insurance_card.png", buf.getvalue())
        self.assertIn("2027-04-20", text)

        source = SourceFile("Receipts/health_insurance_card.png", text, domain="insurance")
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        self.assertEqual(result.commitments[0].date, "2027-04-20")

    def test_scanned_image_pdf_ocr_fallback(self) -> None:
        from PIL import Image, ImageDraw
        from paperclock.readers import extract_text

        img = Image.new("RGB", (650, 180), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((30, 60), "Vehicle Registration Expiry Date: 2028-11-15", fill="black")
        pdf_buf = io.BytesIO()
        img.save(pdf_buf, format="PDF")

        text = extract_text("Scans/vehicle_rc.pdf", pdf_buf.getvalue())
        self.assertIn("2028-11-15", text)

        source = SourceFile("Scans/vehicle_rc.pdf", text, domain="vehicle")
        result = extract_commitments(source, self.today)
        self.assertGreaterEqual(len(result.commitments), 1)
        self.assertIn("2028-11-15", [c.date for c in result.commitments])

    def test_long_single_paragraph_line_segmentation(self) -> None:
        sentences = [
            "This is background paragraph padding text without any action item or commitment date at all." for _ in range(35)
        ]
        sentences.append("Your annual SaaS subscription renewal is due on 2027-06-15.")
        sentences.extend(["More trailing padding text to ensure total line length exceeds twenty-five hundred bytes easily." for _ in range(35)])
        long_line = " ".join(sentences)
        self.assertGreater(len(long_line), 3000)

        source = SourceFile("Contracts/long_line_contract.txt", long_line, domain="subscription")
        result = extract_commitments(source, self.today)
        self.assertEqual(len(result.commitments), 1)
        self.assertEqual(result.commitments[0].date, "2027-06-15")

    def test_anchor_based_year_inference_for_undated_named_months(self) -> None:
        text = "Your membership renewal is due by October 15."
        # If anchor modified date is in 2024, the extracted date should resolve in 2024, not 2026/2027
        source_archived = SourceFile("Archive/old_policy.txt", text, modified="2024-03-01T10:00:00Z", domain="subscription")
        result_archived = extract_commitments(source_archived, self.today)
        # Because October 2024 is in the past relative to today (2026-08-15) and not overdue, it is rejected as historical noise
        self.assertEqual(len(result_archived.commitments), 0)

        # But if anchor is current (2026), it resolves into current/upcoming
        source_current = SourceFile("Current/new_policy.txt", text, modified="2026-08-01T10:00:00Z", domain="subscription")
        result_current = extract_commitments(source_current, self.today)
        self.assertEqual(len(result_current.commitments), 1)
        self.assertEqual(result_current.commitments[0].date, "2026-10-15")


if __name__ == "__main__":
    unittest.main()
