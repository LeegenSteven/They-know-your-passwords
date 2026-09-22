#!/usr/bin/env python3
"""Local, line-delimited JSON service for password-risk inference.

Only protocol JSON is written to stdout. Diagnostics go to stderr and never
include request parameters.
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict

try:
    import tomllib  # type: ignore
except ImportError:
    import tomli as tomllib  # type: ignore

from adapters import AdapterError
from registry import ModelRegistry


ALLOWED_STATUSES = {"OK", "OUT_OF_DOMAIN", "TIMEOUT", "ERROR", "UNAVAILABLE", "LOADING"}


class JsonLineServer:
    def __init__(self, config_path: Path, warmup: bool) -> None:
        self._config_path = config_path.resolve()
        with self._config_path.open("rb") as stream:
            self._config = tomllib.load(stream)
        self._registry = ModelRegistry(self._config, self._config_path.parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="risk-inference")
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ready = not warmup
        self._loading = warmup
        self._load_error = None
        self._stopping = False
        self._pending: Dict[str, Dict[str, Any]] = {}

    def _warmup(self) -> None:
        """Load models on the process main thread.

        PyTorch's Windows CUDA initialization may hold the interpreter while it
        runs. The protocol reader therefore has its own thread and can answer
        the initial LOADING probe before model initialization begins.
        """
        with self._state_lock:
            if not self._loading:
                return
        try:
            self._registry.load_all()
            with self._state_lock:
                self._ready = True
                self._loading = False
        except Exception as error:
            with self._state_lock:
                self._ready = False
                self._loading = False
                self._load_error = type(error).__name__
            print("model warmup failed: %s" % type(error).__name__, file=sys.stderr, flush=True)

    def _write(self, response: Dict[str, Any]) -> None:
        with self._write_lock:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    def _error(self, request_id: Any, status: str, code: str, detail: str = "") -> Dict[str, Any]:
        safe_status = status if status in ALLOWED_STATUSES else "ERROR"
        response = {"id": request_id, "status": safe_status, "error_code": code}
        if detail:
            response["detail"] = detail
        return response

    def _ping(self, request_id: Any) -> Dict[str, Any]:
        with self._state_lock:
            ready = self._ready
            loading = self._loading
            load_error = self._load_error
        if loading:
            return {"id": request_id, "status": "LOADING", "ready": False}
        if not ready:
            return self._error(request_id, "UNAVAILABLE", "MODEL_LOAD_FAILED", load_error or "model load failed")
        descriptions = self._registry.list_models()
        return {
            "id": request_id,
            "status": "OK",
            "ready": True,
            "models": descriptions["models"],
            "defaults": descriptions["defaults"],
            "algorithm_versions": self._registry.model_versions(),
        }

    def _execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise AdapterError("ERROR", "INVALID_REQUEST", "params must be an object")

        if method == "assess_trawling":
            result = self._registry.assess_trawling(
                params.get("candidate"), params.get("model_id"), params.get("options")
            )
        elif method == "assess_reuse":
            history = params.get("history")
            if not isinstance(history, list):
                raise AdapterError("ERROR", "INVALID_REQUEST", "history must be an array")
            result = self._registry.assess_reuse(
                params.get("candidate"), history, params.get("model_id"), params.get("options")
            )
        elif method == "generate_candidates":
            constraints = params.get("constraints") or {}
            if not isinstance(constraints, dict):
                raise AdapterError("ERROR", "INVALID_REQUEST", "constraints must be an object")
            count = params.get("count", 1)
            if not isinstance(count, int) or count < 1 or count > 20:
                raise AdapterError("ERROR", "INVALID_REQUEST", "count must be between 1 and 20")
            result = self._registry.generate_candidates(
                constraints, count, params.get("model_id"), params.get("options")
            )
        else:
            raise AdapterError("ERROR", "UNKNOWN_METHOD", "unknown method")

        response = {"id": request_id, "status": "OK"}
        response.update(result)
        response["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return response

    def _complete(self, request_id: str, future: Future) -> None:
        pending = self._pending.get(request_id)
        if pending is None:
            return
        timer = pending["timer"]
        timer.cancel()
        self._pending.pop(request_id, None)
        if pending["timed_out"]:
            return
        try:
            response = future.result()
        except AdapterError as error:
            response = self._error(request_id, error.status, error.error_code, error.detail)
        except Exception as error:
            print("request failed: %s" % type(error).__name__, file=sys.stderr, flush=True)
            response = self._error(request_id, "ERROR", "INTERNAL_ERROR", "inference failed")
        self._write(response)

    def _timeout(self, request_id: str) -> None:
        pending = self._pending.get(request_id)
        if pending is None:
            return
        pending["timed_out"] = True
        self._write(self._error(request_id, "TIMEOUT", "TIMEOUT", "request exceeded its deadline"))

    def _submit(self, request: Dict[str, Any]) -> None:
        request_id = request["id"]
        timeout_ms = request.get("timeout_ms", self._config.get("service", {}).get("timeout_ms", 3000))
        if not isinstance(timeout_ms, int) or timeout_ms < 1 or timeout_ms > 120000:
            self._write(self._error(request_id, "ERROR", "INVALID_REQUEST", "invalid timeout_ms"))
            return
        if request_id in self._pending:
            self._write(self._error(request_id, "ERROR", "DUPLICATE_REQUEST_ID", "request id is already pending"))
            return
        timer = threading.Timer(timeout_ms / 1000.0, self._timeout, args=(request_id,))
        self._pending[request_id] = {"timer": timer, "timed_out": False}
        future = self._executor.submit(self._execute, request)
        future.add_done_callback(lambda completed: self._complete(request_id, completed))
        timer.start()

    def handle(self, request: Any) -> bool:
        if not isinstance(request, dict):
            self._write(self._error(None, "ERROR", "INVALID_REQUEST", "request must be an object"))
            return True
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            self._write(self._error(None, "ERROR", "INVALID_REQUEST", "id must be a non-empty string"))
            return True
        if not isinstance(method, str):
            self._write(self._error(request_id, "ERROR", "INVALID_REQUEST", "method must be a string"))
            return True
        if method == "ping":
            self._write(self._ping(request_id))
            return True
        if method == "list_models":
            with self._state_lock:
                if self._loading:
                    self._write({"id": request_id, "status": "LOADING", "ready": False})
                    return True
            response = {"id": request_id, "status": "OK"}
            response.update(self._registry.list_models())
            self._write(response)
            return True
        if method == "shutdown":
            self._write({"id": request_id, "status": "OK"})
            self._stopping = True
            return False
        with self._state_lock:
            if self._loading:
                self._write(self._error(request_id, "LOADING", "MODEL_LOADING", "models are loading"))
                return True
            if not self._ready:
                self._write(self._error(request_id, "UNAVAILABLE", "MODEL_LOAD_FAILED", "models are unavailable"))
                return True
        self._submit(request)
        return True

    def _read_requests(self) -> None:
        for raw_line in sys.stdin:
            if self._stopping:
                break
            try:
                request = json.loads(raw_line)
            except json.JSONDecodeError:
                self._write(self._error(None, "ERROR", "INVALID_JSON", "input is not valid JSON"))
                continue
            if not self.handle(request):
                break

    def run(self) -> int:
        if self._loading:
            protocol_thread = threading.Thread(
                target=self._read_requests,
                name="risk-protocol",
                daemon=True,
            )
            protocol_thread.start()
            time.sleep(0.05)
            self._warmup()
            protocol_thread.join()
        else:
            self._read_requests()
        for pending in list(self._pending.values()):
            pending["timer"].cancel()
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._registry.dispose()
        return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local password-risk inference service")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().with_name("config.toml"),
        help="path to TOML configuration",
    )
    parser.add_argument("--no-warmup", action="store_true", help="defer model loading (diagnostics only)")
    return parser.parse_args()


def main() -> int:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    arguments = parse_arguments()
    server = JsonLineServer(arguments.config, warmup=not arguments.no_warmup)
    return server.run()


if __name__ == "__main__":
    raise SystemExit(main())
