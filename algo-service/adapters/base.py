"""Shared model-adapter contract and protocol-safe errors."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Iterable, Optional


class Capability(str, Enum):
    ASSESS_TRAWLING = "ASSESS_TRAWLING"
    ASSESS_REUSE = "ASSESS_REUSE"
    GENERATE_CANDIDATES = "GENERATE_CANDIDATES"


class AdapterError(RuntimeError):
    """An expected failure safe to expose through the local protocol."""

    def __init__(self, status: str, error_code: str, detail: str = "") -> None:
        super().__init__(detail or error_code)
        self.status = status
        self.error_code = error_code
        self.detail = detail


class ModelAdapter(ABC):
    adapter_version = "1"

    def __init__(self) -> None:
        self._loaded = False
        self._load_error: Optional[str] = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    @abstractmethod
    def describe(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def load(self) -> None:
        raise NotImplementedError

    def evaluate(
        self,
        candidate: str,
        history: Iterable[str],
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise AdapterError("ERROR", "CAPABILITY_MISMATCH", "model does not support evaluation")

    def generate_candidates(
        self,
        constraints: Dict[str, Any],
        count: int,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise AdapterError("ERROR", "CAPABILITY_MISMATCH", "model does not support generation")

    def dispose(self) -> None:
        return None

    def public_description(self) -> Dict[str, Any]:
        description = dict(self.describe())
        description["adapter_version"] = self.adapter_version
        description["ready"] = self.loaded
        if self.load_error:
            description["load_error"] = self.load_error
        return description


def validate_printable_ascii(value: Any, minimum: int, maximum: int, field: str) -> str:
    if not isinstance(value, str):
        raise AdapterError("ERROR", "INVALID_REQUEST", "%s must be a string" % field)
    if not minimum <= len(value) <= maximum:
        raise AdapterError(
            "OUT_OF_DOMAIN",
            "OUT_OF_DOMAIN",
            "%s length must be between %d and %d" % (field, minimum, maximum),
        )
    if not all(32 <= ord(character) <= 126 for character in value):
        raise AdapterError(
            "OUT_OF_DOMAIN",
            "OUT_OF_DOMAIN",
            "%s must contain printable ASCII characters only" % field,
        )
    return value
