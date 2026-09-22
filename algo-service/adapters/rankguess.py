"""Read-only adapter around RankGuess's existing inference functions."""

import contextlib
import hashlib
import importlib.util
import json
import os
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import numpy as np
import torch

from .base import AdapterError, Capability, ModelAdapter, validate_printable_ascii


class RankGuessAdapter(ModelAdapter):
    adapter_version = "1"

    def __init__(self, config: Dict[str, Any], service_root: Path) -> None:
        super().__init__()
        self._config = config
        self._service_root = service_root
        self._algorithm_file = self._resolve(config["algorithm_file"])
        self._model_file = self._resolve(config["model_file"])
        self._cache_dir = self._resolve(config.get("cache_dir", "mc_cache"))
        self._sample_size = int(config.get("sample_size", 100000))
        self._batch_size = int(config.get("batch_size", 1000))
        self._seed = int(config.get("seed", 20260922))
        self._device_name = str(config.get("device", "auto"))
        self._module = None
        self._model = None
        self._device = None
        self._ref_probs = None
        self._ref_guesses = None
        self._model_hash = ""
        self._model_version = "rankguess-unknown"

    def _resolve(self, raw_path: str) -> Path:
        path = Path(raw_path)
        return path if path.is_absolute() else (self._service_root / path).resolve()

    def describe(self) -> Dict[str, Any]:
        return {
            "model_id": "rankguess",
            "model_version": self._model_version,
            "algorithm_version": self._model_version,
            "capabilities": [Capability.ASSESS_TRAWLING.value],
            "input_domain": {
                "candidate": {"length": [5, 20], "charset": "ASCII 32-126"},
            },
            "required_context": [],
            "output_metrics": ["guess_number", "probability"],
        }

    def _select_device(self) -> torch.device:
        if self._device_name == "auto":
            return torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if self._device_name == "cuda" and not torch.cuda.is_available():
            raise AdapterError("UNAVAILABLE", "DEVICE_UNAVAILABLE", "CUDA is not available")
        if self._device_name not in ("cpu", "cuda"):
            raise AdapterError("ERROR", "INVALID_CONFIG", "unsupported device")
        return torch.device(self._device_name)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _import_module(self):
        module_name = "risk_rankguess_mc"
        existing = sys.modules.get(module_name)
        if existing is not None:
            return existing
        specification = importlib.util.spec_from_file_location(module_name, str(self._algorithm_file))
        if specification is None or specification.loader is None:
            raise AdapterError("UNAVAILABLE", "MODEL_IMPORT_FAILED", "cannot import RankGuess")
        module = importlib.util.module_from_spec(specification)
        sys.modules[module_name] = module
        with contextlib.redirect_stdout(sys.stderr):
            specification.loader.exec_module(module)
        return module

    def _cache_identity(self) -> Dict[str, Any]:
        return {
            "schema": 1,
            "adapter_version": self.adapter_version,
            "model_sha256": self._model_hash,
            "sample_size": self._sample_size,
            "batch_size": self._batch_size,
            "seed": self._seed,
            "generator_device": "cpu",
            "torch_version": torch.__version__,
        }

    def _cache_path(self) -> Path:
        encoded = json.dumps(self._cache_identity(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        key = hashlib.sha256(encoded).hexdigest()[:20]
        return self._cache_dir / ("rankguess-%s.npz" % key)

    def _load_or_create_reference_table(self) -> None:
        cache_path = self._cache_path()
        if cache_path.is_file():
            with np.load(str(cache_path), allow_pickle=False) as cached:
                metadata = json.loads(str(cached["metadata"].item()))
                if metadata == self._cache_identity():
                    self._ref_probs = np.asarray(cached["ref_probs"], dtype=np.float64)
                    self._ref_guesses = np.asarray(cached["ref_guesses"], dtype=np.float64)
                    return

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        random.seed(self._seed)
        np.random.seed(self._seed)
        torch.manual_seed(self._seed)
        cpu = torch.device("cpu")
        with contextlib.redirect_stdout(sys.stderr):
            reference_model = self._module.load_model(str(self._model_file), cpu)
            probabilities, guesses = self._module.monte_carlo_estimation(
                reference_model,
                cpu,
                sample_size=self._sample_size,
                batch_size=self._batch_size,
            )
        self._ref_probs = np.asarray(probabilities, dtype=np.float64)
        self._ref_guesses = np.asarray(guesses, dtype=np.float64)

        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".npz", prefix="rankguess-", dir=str(self._cache_dir), delete=False
            ) as temporary:
                temporary_name = temporary.name
                np.savez_compressed(
                    temporary,
                    metadata=json.dumps(self._cache_identity(), sort_keys=True),
                    ref_probs=self._ref_probs,
                    ref_guesses=self._ref_guesses,
                )
            os.replace(temporary_name, str(cache_path))
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def load(self) -> None:
        try:
            if not self._algorithm_file.is_file() or not self._model_file.is_file():
                raise AdapterError("UNAVAILABLE", "MODEL_FILE_NOT_FOUND", "RankGuess assets are missing")
            self._module = self._import_module()
            self._model_hash = self._sha256(self._model_file)
            checkpoint = torch.load(str(self._model_file), map_location="cpu", weights_only=True)
            self._model_version = "rankguess-csdn-epoch%s" % checkpoint.get("epoch", "unknown")
            del checkpoint
            self._load_or_create_reference_table()
            self._device = self._select_device()
            with contextlib.redirect_stdout(sys.stderr):
                self._model = self._module.load_model(str(self._model_file), self._device)
            self._loaded = True
            self._load_error = None
        except Exception as error:
            self._loaded = False
            self._load_error = type(error).__name__
            raise

    def evaluate(
        self,
        candidate: str,
        history: Iterable[str],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        del history, options
        validate_printable_ascii(candidate, 5, 20, "candidate")
        if not self.loaded:
            raise AdapterError("UNAVAILABLE", "MODEL_NOT_READY", "RankGuess is not ready")
        with contextlib.redirect_stdout(sys.stderr):
            scored = self._module.calculate_probability_batch(self._model, [candidate], self._device)
        if len(scored) != 1:
            raise AdapterError("ERROR", "INVALID_MODEL_OUTPUT", "RankGuess returned no score")
        probability = float(scored[0][1])
        raw_guess_number = float(
            self._module.get_guess_number(probability, self._ref_probs, self._ref_guesses)
        )
        if not np.isfinite(probability) or probability <= 0 or not np.isfinite(raw_guess_number):
            raise AdapterError("ERROR", "INVALID_MODEL_OUTPUT", "RankGuess returned an invalid numeric result")
        guess_number = max(1.0, raw_guess_number)
        table_capped = bool(probability < float(self._ref_probs[-1]))
        return {
            "mode": "TRAWLING",
            "model_id": "rankguess",
            "model_version": self._model_version,
            "algorithm_version": self._model_version,
            "metric_type": "estimated_guess_number",
            "native": {
                "guess_number": guess_number,
                "probability": probability,
                "table_size": int(len(self._ref_probs)),
                "clamped_low": raw_guess_number < 1.0,
                "table_capped": table_capped,
            },
        }

    def dispose(self) -> None:
        self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
