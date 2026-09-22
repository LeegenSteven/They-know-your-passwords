"""Model registry and request dispatch for the local service."""

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from adapters import AdapterError, Capability, PardAdapter, RankGuessAdapter


class ModelRegistry:
    def __init__(self, config: Dict[str, Any], service_root: Path) -> None:
        self._defaults = config.get("models", {})
        self._adapters = {
            "rankguess": RankGuessAdapter(config["rankguess"], service_root),
            "pard-glca": PardAdapter(config["pard"], service_root),
        }
        self._host_generator = {
            "model_id": "keepassxc-password-generator",
            "model_version": "host",
            "algorithm_version": "host",
            "adapter_version": "1",
            "capabilities": [Capability.GENERATE_CANDIDATES.value],
            "input_domain": {"constraints": "KeePassXC PasswordGenerator rules"},
            "required_context": [],
            "output_metrics": ["candidates"],
            "provider": "HOST",
            "ready": True,
        }

    def load_all(self) -> None:
        errors = []
        for model_id, adapter in self._adapters.items():
            try:
                adapter.load()
            except Exception as error:
                errors.append((model_id, type(error).__name__))
        if errors:
            summary = ", ".join("%s:%s" % item for item in errors)
            raise AdapterError("UNAVAILABLE", "MODEL_LOAD_FAILED", summary)

    def list_models(self) -> Dict[str, Any]:
        models = [adapter.public_description() for adapter in self._adapters.values()]
        models.append(dict(self._host_generator))
        return {
            "models": models,
            "defaults": {
                "trawling_model_id": self._defaults.get("trawling_model_id", "rankguess"),
                "reuse_model_id": self._defaults.get("reuse_model_id", "pard-glca"),
                "generator_model_id": self._defaults.get(
                    "generator_model_id", "keepassxc-password-generator"
                ),
            },
        }

    def model_versions(self) -> Dict[str, str]:
        return {
            model_id: str(adapter.describe().get("model_version", "unknown"))
            for model_id, adapter in self._adapters.items()
        }

    def _get_adapter(self, model_id: str, required_capability: Capability):
        adapter = self._adapters.get(model_id)
        if adapter is None:
            if model_id == self._host_generator["model_id"]:
                raise AdapterError(
                    "UNAVAILABLE",
                    "HOST_GENERATOR_REQUIRED",
                    "candidate generation is provided by the KeePassXC host",
                )
            raise AdapterError("UNAVAILABLE", "MODEL_NOT_FOUND", "requested model is not registered")
        if required_capability.value not in adapter.describe().get("capabilities", []):
            raise AdapterError("ERROR", "CAPABILITY_MISMATCH", "model does not provide required capability")
        if not adapter.loaded:
            raise AdapterError("UNAVAILABLE", "MODEL_NOT_READY", "requested model is not ready")
        return adapter

    def assess_trawling(
        self, candidate: str, model_id: Optional[str] = None, options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        selected = model_id or self._defaults.get("trawling_model_id", "rankguess")
        adapter = self._get_adapter(selected, Capability.ASSESS_TRAWLING)
        return adapter.evaluate(candidate, [], options)

    def assess_reuse(
        self,
        candidate: str,
        history: Iterable[str],
        model_id: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected = model_id or self._defaults.get("reuse_model_id", "pard-glca")
        adapter = self._get_adapter(selected, Capability.ASSESS_REUSE)
        return adapter.evaluate(candidate, history, options)

    def generate_candidates(
        self,
        constraints: Dict[str, Any],
        count: int,
        model_id: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected = model_id or self._defaults.get(
            "generator_model_id", "keepassxc-password-generator"
        )
        adapter = self._get_adapter(selected, Capability.GENERATE_CANDIDATES)
        return adapter.generate_candidates(constraints, count, options)

    def dispose(self) -> None:
        for adapter in self._adapters.values():
            adapter.dispose()
