from __future__ import annotations

import unittest

from paperclock.calendar import make_calendar


class CalendarTests(unittest.TestCase):
    def test_calendar_is_valid_and_escapes_text(self) -> None:
        output = make_calendar(
            [{
                "id": "abc123",
                "date": "2026-09-10",
                "title": "Cancel plan, keep receipt",
                "source": "Bills/energy.txt",
                "page": 4,
                "reason": "renewal found nearby",
                "category": "cancellation",
            }]
        )
        self.assertIn("BEGIN:VCALENDAR\r\n", output)
        self.assertIn("DTSTART;VALUE=DATE:20260910", output)
        self.assertIn("SUMMARY:Cancel plan\\, keep receipt", output)
        self.assertIn("Found in Bills/energy.txt\\, page 4", output)
        self.assertTrue(output.endswith("END:VCALENDAR\r\n"))


if __name__ == "__main__":
    unittest.main()
