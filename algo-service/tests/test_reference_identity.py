"""A CPU-thread change must rebuild a probability-only reference table."""
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, mock
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters.rankguess import RankGuessAdapter


class ReferenceIdentityTests(TestCase):
    def test_changed_cpu_threads_rebuild_and_same_identity_reuses(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = RankGuessAdapter({"algorithm_file": "unused.py", "model_file": "unused.bin",
                                        "sample_size": 2, "batch_size": 2}, Path(directory))
            module = mock.Mock()
            module.monte_carlo_estimation.return_value = (np.array([.1, .01]), np.array([10., 100.]))
            adapter._module = module
            adapter._load_or_create_reference_table()
            adapter._load_or_create_reference_table()
            self.assertEqual(module.monte_carlo_estimation.call_count, 1)
            changed = RankGuessAdapter({"algorithm_file": "unused.py", "model_file": "unused.bin",
                                       "sample_size": 2, "batch_size": 2, "reference_cpu_threads": 2}, Path(directory))
            changed._module = module
            changed._load_or_create_reference_table()
            self.assertEqual(module.monte_carlo_estimation.call_count, 2)
            self.assertEqual(len(list(Path(directory, "mc_cache").glob("*.npz"))), 2)
