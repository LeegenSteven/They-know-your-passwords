import sys
import unittest
from pathlib import Path


SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from adapters.base import AdapterError, validate_printable_ascii


class DomainValidationTests(unittest.TestCase):
    def test_accepts_printable_ascii_boundaries(self):
        minimum_value = "x" * 5
        maximum_value = " " * 20
        self.assertEqual(validate_printable_ascii(minimum_value, 5, 20, "candidate"), minimum_value)
        self.assertEqual(validate_printable_ascii(maximum_value, 5, 20, "candidate"), maximum_value)

    def test_rejects_length_and_charset(self):
        values = ("x" * 3, "x" * 21, "\u6d4b\u8bd5\u5b57\u7b26")
        for value in values:
            with self.subTest(value_length=len(value)):
                with self.assertRaises(AdapterError) as raised:
                    validate_printable_ascii(value, 5, 20, "candidate")
                self.assertEqual(raised.exception.status, "OUT_OF_DOMAIN")
                self.assertEqual(raised.exception.error_code, "OUT_OF_DOMAIN")
                self.assertNotIn(value, raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
