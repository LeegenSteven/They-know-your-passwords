import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adapters"))
from psm import estimate_interval


class PsmTests(unittest.TestCase):
    def test_all_six_boundaries(self):
        for index, boundary in enumerate((1, 10**3, 10**6, 10**9, 10**12, 10**15)):
            self.assertEqual(estimate_interval(boundary)["band"], index + 1)
            if index:
                self.assertEqual(estimate_interval(math.nextafter(float(boundary), 0))["band"], index)

    def test_capped_is_lower_bound_without_band(self):
        result = estimate_interval(10**17, True)
        self.assertIsNone(result["band"])
        self.assertEqual(result["lower_bound"], 10**17)
        self.assertFalse(result["calibrated"])
        self.assertEqual(result["evidence"], "REFERENCE_TABLE_LOWER_BOUND")

    def test_invalid_numbers(self):
        for value in (0, float("inf"), float("nan"), -1):
            with self.assertRaises(ValueError):
                estimate_interval(value)
