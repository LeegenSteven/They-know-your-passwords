"""Async client for the existing local JSON Lines algorithm service."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any


JsonObject = dict[str, Any]


class AlgorithmServiceError(RuntimeError):
    """A sanitized bridge error that never contains request parameters."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class AlgorithmServiceClient:
    """Own one algorithm-service child process and serialize requests to it."""

    def __init__(
        self,
        python_path: Path,
        service_path: Path,
        config_path: Path,
        *,
        startup_timeout_ms: int = 180_000,
    ) -> None:
        self.python_path = python_path.resolve()
        self.service_path = service_path.resolve()
        self.config_path = config_path.resolve()
        self.startup_timeout_ms = startup_timeout_ms
        self._process: asyncio.subprocess.Process | None = None
        self._request_lock = asyncio.Lock()

    @classmethod
    def from_environment(cls) -> "AlgorithmServiceClient":
        project_root = Path(__file__).resolve().parents[2]
        default_model_python = Path(r"D:\Anaconda3\envs\pytorch_cuda\python.exe")
        python_path = Path(
            os.environ.get(
                "TYP_ALGORITHM_PYTHON",
                str(default_model_python if default_model_python.exists() else Path(sys.executable)),
            )
        )
        service_path = Path(
            os.environ.get("TYP_ALGORITHM_SERVER", str(project_root / "algo-service" / "server.py"))
        )
        config_path = Path(
            os.environ.get("TYP_ALGORITHM_CONFIG", str(project_root / "algo-service" / "config.toml"))
        )
        raw_timeout = os.environ.get("TYP_MCP_STARTUP_TIMEOUT_MS", "180000")
        try:
            startup_timeout_ms = min(max(int(raw_timeout), 1_000), 600_000)
        except ValueError:
            startup_timeout_ms = 180_000
        return cls(python_path, service_path, config_path, startup_timeout_ms=startup_timeout_ms)

    def configuration_status(self) -> JsonObject:
        return {
            "python_configured": self.python_path.is_file(),
            "service_configured": self.service_path.is_file(),
            "config_configured": self.config_path.is_file(),
        }

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _start_unlocked(self) -> None:
        if self.running:
            return
        status = self.configuration_status()
        if not status["python_configured"]:
            raise AlgorithmServiceError("ALGORITHM_PYTHON_NOT_FOUND", "algorithm Python is not configured")
        if not status["service_configured"]:
            raise AlgorithmServiceError("ALGORITHM_SERVER_NOT_FOUND", "algorithm service is not configured")
        if not status["config_configured"]:
            raise AlgorithmServiceError("ALGORITHM_CONFIG_NOT_FOUND", "algorithm configuration is not available")

        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = await asyncio.create_subprocess_exec(
                str(self.python_path),
                "-u",
                str(self.service_path),
                "--config",
                str(self.config_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError as error:
            raise AlgorithmServiceError("ALGORITHM_START_FAILED", "algorithm service could not be started") from error

    async def _discard_process_unlocked(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

    async def _exchange_unlocked(
        self,
        method: str,
        params: JsonObject | None,
        timeout_ms: int,
    ) -> JsonObject:
        await self._start_unlocked()
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise AlgorithmServiceError("ALGORITHM_UNAVAILABLE", "algorithm service is unavailable")
        if process.returncode is not None:
            self._process = None
            raise AlgorithmServiceError("ALGORITHM_EXITED", "algorithm service exited unexpectedly")

        request_id = uuid.uuid4().hex
        request = {
            "id": request_id,
            "method": method,
            "timeout_ms": timeout_ms,
            "params": params or {},
        }
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            process.stdin.write(encoded)
            await process.stdin.drain()
            raw_response = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=max(1.0, timeout_ms / 1000.0 + 1.0),
            )
        except (BrokenPipeError, ConnectionError) as error:
            await self._discard_process_unlocked()
            raise AlgorithmServiceError("ALGORITHM_UNAVAILABLE", "algorithm service connection failed") from error
        except asyncio.TimeoutError as error:
            await self._discard_process_unlocked()
            raise AlgorithmServiceError("ALGORITHM_BRIDGE_TIMEOUT", "algorithm service did not respond") from error

        if not raw_response:
            await self._discard_process_unlocked()
            raise AlgorithmServiceError("ALGORITHM_EXITED", "algorithm service closed its output")
        try:
            response = json.loads(raw_response)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            await self._discard_process_unlocked()
            raise AlgorithmServiceError("INVALID_ALGORITHM_RESPONSE", "algorithm service returned invalid JSON") from error
        if not isinstance(response, dict) or response.get("id") != request_id:
            await self._discard_process_unlocked()
            raise AlgorithmServiceError("INVALID_ALGORITHM_RESPONSE", "algorithm service returned an invalid response")
        response.pop("id", None)
        return response

    async def call(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        timeout_ms: int = 3_000,
    ) -> JsonObject:
        async with self._request_lock:
            return await self._exchange_unlocked(method, params, timeout_ms)

    async def wait_until_ready(self, timeout_ms: int | None = None) -> JsonObject:
        budget_ms = timeout_ms if timeout_ms is not None else self.startup_timeout_ms
        deadline = time.monotonic() + budget_ms / 1000.0
        last_response: JsonObject = {"status": "LOADING", "ready": False}
        while time.monotonic() < deadline:
            last_response = await self.call("ping", timeout_ms=2_000)
            if last_response.get("status") != "LOADING":
                return last_response
            await asyncio.sleep(0.25)
        return {
            "status": "TIMEOUT",
            "ready": False,
            "error_code": "MODEL_STARTUP_TIMEOUT",
            "detail": "models did not become ready before the local deadline",
        }

    async def close(self) -> None:
        async with self._request_lock:
            process = self._process
            if process is None:
                return
            if process.returncode is None:
                try:
                    await self._exchange_unlocked("shutdown", {}, 1_000)
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except (AlgorithmServiceError, asyncio.TimeoutError):
                    await self._discard_process_unlocked()
            self._process = None
