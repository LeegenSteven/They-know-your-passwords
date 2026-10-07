"""Numerical PSM intervals, independent of empirical risk calibration."""

import math
from typing import Any, Dict


def estimate_interval(guess_number: float, table_capped: bool = False) -> Dict[str, Any]:
    if not math.isfinite(guess_number) or guess_number < 1:
        raise ValueError("invalid guess estimate")
    if table_capped:
        return {"band": None, "lower_bound": guess_number, "upper_bound": None,
                "evidence": "REFERENCE_TABLE_LOWER_BOUND", "calibrated": False}
    bounds = (1, 10**3, 10**6, 10**9, 10**12, 10**15)
    band = next((i for i in range(5) if guess_number < bounds[i + 1]), 5)
    return {"band": band + 1, "lower_bound": bounds[band],
            "upper_bound": bounds[band + 1] if band < 5 else None,
            "evidence": "ESTIMATED_UNCALIBRATED", "calibrated": False}
