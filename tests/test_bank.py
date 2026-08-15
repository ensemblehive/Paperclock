from __future__ import annotations

import unittest
import io
import zipfile
from datetime import date
from unittest.mock import patch

from paperclock.bank import analyze_bank_statement
from paperclock.scanner import scan_file
from paperclock.readers import _read_xlsx


class BankStatementAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 8, 15)

    def test_csv_statement_analysis(self) -> None:
        csv_content = """HDFC Bank Account Statement of Account
Date,Particulars,Chq/Ref No,Withdrawal,Deposit,Balance
01/08/2026,Salary Credit Infosys Ltd,SAL-9910,,85000.00,125000.00
03/08/2026,Rent to Landlord Society Maint,UPI-1029,25000.00,,100000.00
05/08/2026,Swiggy Food Delivery,UPI-3341,850.00,,99150.00
07/08/2026,Electricity Power Bill,BILL-990,2400.00,,96750.00
10/08/2026,Netflix Subscription,CARD-441,649.00,,96101.00
"""
        summary = analyze_bank_statement(csv_content)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.currency, "₹")
        self.assertEqual(summary.total_income, 85000.0)
        self.assertEqual(summary.total_expense, 28899.0)
        self.assertEqual(summary.net_cashflow, 56101.0)
        self.assertEqual(summary.transaction_count, 5)
        self.assertEqual(summary.statement_start, "2026-08-01")
        self.assertEqual(summary.statement_end, "2026-08-10")
        self.assertEqual(summary.opening_balance, 40000.0)
        self.assertEqual(summary.closing_balance, 96101.0)
        self.assertEqual(summary.verification, "verified")
        self.assertEqual(summary.credit_count, 1)
        self.assertEqual(summary.debit_count, 4)
        self.assertEqual(summary.top_expenses[0]["description"], "Rent to Landlord Society Maint")

        # Check category classifications
        cats = {c["category"]: c["amount"] for c in summary.categories}
        self.assertEqual(cats.get("Rent & Housing"), 25000.0)
        self.assertEqual(cats.get("Utilities & Bills"), 2400.0)
        self.assertEqual(cats.get("Food & Groceries"), 850.0)
        self.assertEqual(cats.get("Subscriptions"), 649.0)

    def test_pdf_pipe_delimited_statement_analysis(self) -> None:
        statement_text = """Account Statement | Chase Bank
Statement Period: 01/07/2026 to 31/07/2026 | Opening Balance: $5,200.00 | Closing Balance: $7,450.00
2026-07-02 | Direct Dep Payroll ACME Corp | 4500.00 | CR
2026-07-05 | Apartment Rent payment | 1800.00 | DR
2026-07-12 | Verizon Wireless Bill | 120.00 | DR
2026-07-18 | Spotify USA | 14.99 | DR
2026-07-22 | Uber Ride Airport | 45.50 | DR
"""
        summary = analyze_bank_statement(statement_text)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.currency, "$")
        self.assertEqual(summary.total_income, 4500.0)
        self.assertEqual(summary.total_expense, 1980.49)
        self.assertEqual(summary.net_cashflow, 2519.51)

    def test_column_mapping_ignores_reference_numbers_and_uses_balance_checks(self) -> None:
        statement = """Account Statement,Opening Balance 1000.00,Closing Balance 1400.00
Date,Description,Reference,Debit,Credit,Balance
01/08/2026,Salary,9988776655,,500.00,1500.00
02/08/2026,Groceries,1122334455,100.00,,1400.00
"""
        summary = analyze_bank_statement(statement)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.total_income, 500.0)
        self.assertEqual(summary.total_expense, 100.0)
        self.assertEqual(summary.verification, "verified")

    def test_financial_words_without_statement_structure_are_rejected(self) -> None:
        prose = "A tutorial explains debit and credit, account balances, deposits, and withdrawals."
        self.assertIsNone(analyze_bank_statement(prose))

    def test_repeated_payments_and_largest_credit_become_insights(self) -> None:
        statement = """Account Statement
Date,Description,Debit,Credit,Balance
01/08/2026,Salary ACME,,3000.00,5000.00
02/08/2026,Netflix Card 1234,19.99,,4980.01
12/08/2026,Groceries,120.00,,4860.01
22/08/2026,Netflix Card 9876,19.99,,4840.02
"""
        summary = analyze_bank_statement(statement)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.largest_credit["description"], "Salary ACME")
        self.assertEqual(summary.recurring_payments[0]["count"], 2)
        self.assertEqual(summary.recurring_payments[0]["amount"], 19.99)

    def test_xlsx_blank_columns_do_not_shift_credit_into_debit(self) -> None:
        strings = ["Account Statement", "Date", "Description", "Debit", "Credit", "Balance", "Salary", "Groceries"]
        shared = "".join(f"<si><t>{value}</t></si>" for value in strings)
        sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>
        <row r="1"><c r="A1" t="s"><v>0</v></c></row>
        <row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2" t="s"><v>2</v></c><c r="C2" t="s"><v>3</v></c><c r="D2" t="s"><v>4</v></c><c r="E2" t="s"><v>5</v></c></row>
        <row r="3"><c r="A3"><v>46235</v></c><c r="B3" t="s"><v>6</v></c><c r="D3"><v>500</v></c><c r="E3"><v>1500</v></c></row>
        <row r="4"><c r="A4"><v>46236</v></c><c r="B4" t="s"><v>7</v></c><c r="C4"><v>100</v></c><c r="E4"><v>1400</v></c></row>
        </sheetData></worksheet>"""
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{shared}</sst>')
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        content = _read_xlsx("statement.xlsx", payload.getvalue())
        summary = analyze_bank_statement(content)
        self.assertIsNotNone(summary)
        self.assertEqual(summary.total_income, 500.0)
        self.assertEqual(summary.total_expense, 100.0)

    def test_bank_statement_extractor_integration(self) -> None:
        statement_content = """SBI Bank Statement of Account
Account Statement for Period 01-08-2026 to 31-08-2026. Account: SBI-00992144.
Payment due date: 2026-09-05.
01-08-2026 | Salary Credit TCS | 75000.00 CR
04-08-2026 | Society Rent Transfer | 20000.00 DR
08-08-2026 | Airtel Broadband Fiber | 1199.00 DR
"""
        with patch("paperclock.scanner.decode_upload", return_value=statement_content):
            commitments, _, warning, statements = scan_file({"path": "Bank/sbi_statement.csv"}, today=self.today)
        self.assertIsNone(warning)
        self.assertEqual(len(commitments), 1)
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements[0]["currency"], "₹")
        self.assertEqual(statements[0]["total_income"], 75000.0)


if __name__ == "__main__":
    unittest.main()
