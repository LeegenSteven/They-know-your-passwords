"""Read-only adapter around the PARD source-conditioned student model."""

import contextlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import torch
import torch.nn.functional as functional

from .base import AdapterError, Capability, ModelAdapter, validate_printable_ascii


class PardAdapter(ModelAdapter):
    adapter_version = "1"

    def __init__(self, config: Dict[str, Any], service_root: Path) -> None:
        super().__init__()
        self._config = config
        self._service_root = service_root
        self._algorithm_dir = self._resolve(config["algorithm_dir"])
        self._checkpoint_file = self._resolve(config["model_file"])
        self._device_name = str(config.get("device", "auto"))
        self._beam_width = int(config.get("beam_width", 100))
        self._top_k = int(config.get("top_k", 200))
        self._use_amp = bool(config.get("use_amp", True))
        self._eval_module = None
        self._demo_module = None
        self._model = None
        self._device = None
        self._model_version = "pard-glca-unknown"

    def _resolve(self, raw_path: str) -> Path:
        path = Path(raw_path)
        return path if path.is_absolute() else (self._service_root / path).resolve()

    def describe(self) -> Dict[str, Any]:
        return {
            "model_id": "pard-glca",
            "model_version": self._model_version,
            "algorithm_version": self._model_version,
            "capabilities": [Capability.ASSESS_REUSE.value],
            "input_domain": {
                "candidate": {"length": [1, 20], "charset": "ASCII 32-126"},
                "source": {"length": [1, 20], "charset": "ASCII 32-126"},
            },
            "required_context": ["history"],
            "output_metrics": ["rank", "conditional_log_probability"],
            "inference": {"batch": 1, "beam_width": self._beam_width, "top_k": self._top_k},
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
    def _load_module(name: str, path: Path):
        existing = sys.modules.get(name)
        if existing is not None:
            return existing
        specification = importlib.util.spec_from_file_location(name, str(path))
        if specification is None or specification.loader is None:
            raise AdapterError("UNAVAILABLE", "MODEL_IMPORT_FAILED", "cannot import PARD")
        module = importlib.util.module_from_spec(specification)
        sys.modules[name] = module
        with contextlib.redirect_stdout(sys.stderr):
            specification.loader.exec_module(module)
        return module

    def load(self) -> None:
        try:
            demo_path = self._algorithm_dir / "demo6_glca.py"
            eval_path = self._algorithm_dir / "eval6_glca.py"
            if not demo_path.is_file() or not eval_path.is_file() or not self._checkpoint_file.is_file():
                raise AdapterError("UNAVAILABLE", "MODEL_FILE_NOT_FOUND", "PARD assets are missing")
            algorithm_dir_text = str(self._algorithm_dir)
            if algorithm_dir_text not in sys.path:
                sys.path.insert(0, algorithm_dir_text)
            self._demo_module = self._load_module("demo6_glca", demo_path)
            self._eval_module = self._load_module("risk_pard_eval", eval_path)
            checkpoint = torch.load(str(self._checkpoint_file), map_location="cpu", weights_only=True)
            if checkpoint.get("architecture") != self._demo_module.GLCA_ARCHITECTURE:
                raise AdapterError("UNAVAILABLE", "MODEL_ARCHITECTURE_MISMATCH", "unexpected PARD architecture")
            config = self._eval_module._load_checkpoint_config(str(self._checkpoint_file))
            self._model = self._eval_module.build_model_from_config(config)
            self._model.load_state_dict(checkpoint["model_state"], strict=True)
            self._device = self._select_device()
            self._model = self._model.to(self._device).eval()
            self._model_version = "pard-glca-csdn-e%s" % checkpoint.get("epoch", "unknown")
            self._loaded = True
            self._load_error = None
        except Exception as error:
            self._loaded = False
            self._load_error = type(error).__name__
            raise

    def _candidate_log_probability(self, source: str, candidate: str) -> float:
        batch_class = self._eval_module.Batch
        graph = self._demo_module.build_graph(
            self._demo_module.password_to_indices(source),
            self._demo_module.password_to_indices(source),
            use_seq=False,
            use_align=False,
            use_match=False,
        )
        batch = batch_class.from_data_list([graph]).to(self._device)
        target = self._demo_module.encode_tgt(candidate)
        if target is None:
            raise AdapterError("OUT_OF_DOMAIN", "OUT_OF_DOMAIN", "candidate is outside PARD's input domain")
        target = target.unsqueeze(0).to(self._device)
        amp_enabled = self._use_amp and self._device.type == "cuda"
        with torch.no_grad():
            with torch.autocast(device_type=self._device.type, enabled=amp_enabled, dtype=torch.float16):
                memory, memory_padding = self._model.encode(batch)
                logits = self._model.decode(target[:, :-1], memory, memory_padding)
            log_probabilities = functional.log_softmax(logits.float(), dim=-1)
            expected = target[:, 1:]
            selected = log_probabilities.gather(2, expected.unsqueeze(-1)).squeeze(-1)
            mask = expected.ne(self._demo_module.TGT_PAD)
            return float(selected.masked_fill(~mask, 0.0).sum().item())

    def evaluate(
        self,
        candidate: str,
        history: Iterable[str],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        validate_printable_ascii(candidate, 1, 20, "candidate")
        sources = list(history)
        if not sources:
            raise AdapterError("ERROR", "MISSING_CONTEXT", "history is required for reuse assessment")
        valid_sources: List[str] = []
        skipped_sources = 0
        for source in sources:
            try:
                valid_sources.append(validate_printable_ascii(source, 1, 20, "history item"))
            except AdapterError as error:
                if error.status != "OUT_OF_DOMAIN":
                    raise
                skipped_sources += 1
        if not valid_sources:
            raise AdapterError(
                "OUT_OF_DOMAIN",
                "UNUSABLE_HISTORY",
                "all history items are outside PARD's input domain",
            )
        if not self.loaded:
            raise AdapterError("UNAVAILABLE", "MODEL_NOT_READY", "PARD is not ready")

        selected_beam_width = int((options or {}).get("beam_width", self._beam_width))
        selected_top_k = int((options or {}).get("top_k", self._top_k))
        if selected_beam_width < 1 or selected_beam_width > 200:
            raise AdapterError("ERROR", "INVALID_REQUEST", "beam_width must be between 1 and 200")
        if selected_top_k < 1 or selected_top_k > 500:
            raise AdapterError("ERROR", "INVALID_REQUEST", "top_k must be between 1 and 500")

        for source_index, source in enumerate(valid_sources):
            if candidate == source:
                return self._result(
                    exact_match=True,
                    rank=1,
                    source_index=source_index,
                    candidate_log_probability=None,
                    usable_sources=len(valid_sources),
                    skipped_sources=skipped_sources,
                    top_k=selected_top_k,
                )

        best_rank = None
        best_source_index = None
        best_log_probability = None
        for source_index, source in enumerate(valid_sources):
            with contextlib.redirect_stdout(sys.stderr):
                candidates = self._eval_module.fast_batched_beam_search(
                    self._model,
                    [source],
                    self._device,
                    beam_width=selected_beam_width,
                    top_n=selected_top_k,
                    use_amp=self._use_amp,
                    use_seq_edge=False,
                    use_align_edge=False,
                    use_match_edge=False,
                )[0]
            rank = next((index + 1 for index, item in enumerate(candidates) if item[0] == candidate), None)
            log_probability = self._candidate_log_probability(source, candidate)
            if rank is not None and (best_rank is None or rank < best_rank):
                best_rank = rank
                best_source_index = source_index
                best_log_probability = log_probability
            elif best_rank is None and (
                best_log_probability is None or log_probability > best_log_probability
            ):
                best_source_index = source_index
                best_log_probability = log_probability

        return self._result(
            exact_match=False,
            rank=best_rank,
            source_index=best_source_index,
            candidate_log_probability=best_log_probability,
            usable_sources=len(valid_sources),
            skipped_sources=skipped_sources,
            top_k=selected_top_k,
        )

    def _result(
        self,
        exact_match: bool,
        rank: Optional[int],
        source_index: Optional[int],
        candidate_log_probability: Optional[float],
        usable_sources: int,
        skipped_sources: int,
        top_k: int,
    ) -> Dict[str, Any]:
        return {
            "mode": "REUSE",
            "model_id": "pard-glca",
            "model_version": self._model_version,
            "algorithm_version": self._model_version,
            "metric_type": "conditional_rank",
            "native": {
                "exact_match": exact_match,
                "in_top_k": rank is not None,
                "best_rank": rank,
                "best_source_index": source_index,
                "top_k": top_k,
                "candidate_logprob": candidate_log_probability,
                "usable_sources": usable_sources,
                "skipped_sources": skipped_sources,
            },
        }

    def dispose(self) -> None:
        self._model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
