#!/usr/bin/env python3
"""End-to-end checks against the real local models without logging inputs."""

import argparse
import csv
import json
import math
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, Tuple


def parse_arguments() -> argparse.Namespace:
    service_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--config", type=Path, default=service_root / "config.toml")
    parser.add_argument(
        "--rankguess-data",
        type=Path,
        default=Path(r"D:\研究生\笔记\benchmark\benchmark\test\test_csdn.txt"),
    )
    parser.add_argument(
        "--pard-data",
        type=Path,
        default=Path(r"D:\研究生\HGN\data\csdn_test.jsonl"),
    )
    parser.add_argument("--startup-timeout", type=float, default=180.0)
    parser.add_argument(
        "--skip-derived",
        action="store_true",
        help="skip the comparatively expensive non-exact PARD beam-search check",
    )
    return parser.parse_args()


def valid_ascii(value: Any, minimum: int) -> bool:
    return isinstance(value, str) and minimum <= len(value) <= 20 and all(
        32 <= ord(character) <= 126 for character in value
    )


def load_rankguess_input(path: Path) -> str:
    with path.open("r", encoding="utf-8-sig", errors="strict") as stream:
        for raw_line in stream:
            value = raw_line.rstrip("\r\n")
            if valid_ascii(value, 5):
                return value
    raise RuntimeError("RankGuess data contains no valid input")


def load_pard_pair(path: Path) -> Tuple[str, str]:
    with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as stream:
        if path.suffix.lower() == ".csv":
            rows = csv.reader(stream)
            for row in rows:
                try:
                    record = json.loads(row[0]) if len(row) == 1 else row
                except (json.JSONDecodeError, IndexError):
                    continue
                if isinstance(record, list) and len(record) >= 2:
                    source, target = record[:2]
                    if source != target and valid_ascii(source, 1) and valid_ascii(target, 1):
                        return source, target
        else:
            for raw_line in stream:
                try:
                    record = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    source, target = record.get("src"), record.get("tgt")
                elif isinstance(record, list) and len(record) >= 2:
                    source, target = record[:2]
                else:
                    continue
                if source != target and valid_ascii(source, 1) and valid_ascii(target, 1):
                    return source, target
    raise RuntimeError("PARD data contains no valid non-identical pair")


class ServiceProcess:
    def __init__(self, python: str, config: Path) -> None:
        self._responses: "Queue[Dict[str, Any]]" = Queue()
        self.stderr_lines = []
        server = Path(__file__).resolve().parents[1] / "server.py"
        self.process = subprocess.Popen(
            [python, "-u", str(server), "--config", str(config)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError as error:
                self._responses.put({"_invalid_stdout": str(error)})
                continue
            self._responses.put(parsed)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip("\r\n"))

    def request(self, method: str, params=None, timeout_ms: int = 15000) -> Dict[str, Any]:
        request_id = str(uuid.uuid4())
        request = {"id": request_id, "method": method, "timeout_ms": timeout_ms, "params": params or {}}
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + max(5.0, timeout_ms / 1000.0 + 2.0)
        deferred = []
        while time.monotonic() < deadline:
            try:
                response = self._responses.get(timeout=0.5)
            except Empty:
                continue
            if "_invalid_stdout" in response:
                raise AssertionError("stdout contained a non-JSON line")
            if response.get("id") == request_id:
                for item in deferred:
                    self._responses.put(item)
                return response
            deferred.append(response)
        raise AssertionError("timed out waiting for %s" % method)

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.request("shutdown", timeout_ms=5000)
            except Exception:
                self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def assert_status(response: Dict[str, Any], status: str) -> None:
    if response.get("status") != status:
        safe_response = {key: value for key, value in response.items() if key not in ("candidate", "history")}
        raise AssertionError("expected %s, got %s" % (status, safe_response))


def main() -> int:
    arguments = parse_arguments()
    rankguess_input = load_rankguess_input(arguments.rankguess_data)
    pard_source, pard_target = load_pard_pair(arguments.pard_data)
    service = ServiceProcess(arguments.python, arguments.config)
    try:
        print("phase=warmup", file=sys.stderr, flush=True)
        time.sleep(1.0)
        deadline = time.monotonic() + arguments.startup_timeout
        ping = None
        while time.monotonic() < deadline:
            try:
                ping = service.request("ping", timeout_ms=1000)
            except AssertionError:
                if service.process.poll() is not None:
                    raise AssertionError("service exited during warmup")
                continue
            if ping.get("status") == "OK":
                break
            if ping.get("status") != "LOADING":
                raise AssertionError("service failed to warm up: %s" % ping)
            time.sleep(0.25)
        assert_status(ping or {}, "OK")

        print("phase=rankguess", file=sys.stderr, flush=True)
        trawling = service.request("assess_trawling", {"candidate": rankguess_input})
        assert_status(trawling, "OK")
        if not math.isfinite(trawling["native"]["guess_number"]):
            raise AssertionError("RankGuess returned a non-finite guess number")

        for candidate in ("x" * 3, "\u6d4b\u8bd5" * 3, "x" * 25):
            assert_status(service.request("assess_trawling", {"candidate": candidate}), "OUT_OF_DOMAIN")

        print("phase=pard-exact", file=sys.stderr, flush=True)
        exact = service.request(
            "assess_reuse", {"candidate": pard_source, "history": [pard_source]}
        )
        assert_status(exact, "OK")
        if not exact["native"]["exact_match"]:
            raise AssertionError("exact reuse was not detected")

        derived = None
        if not arguments.skip_derived:
            print("phase=pard-derived", file=sys.stderr, flush=True)
            derived = service.request(
                "assess_reuse",
                {"candidate": pard_target, "history": [pard_source]},
                timeout_ms=120000,
            )
            assert_status(derived, "OK")
            if not isinstance(derived["native"]["candidate_logprob"], float):
                raise AssertionError("PARD did not return a conditional score")

        unusable = service.request(
            "assess_reuse", {"candidate": "x", "history": ["\u6d4b\u8bd5" * 8]}
        )
        assert_status(unusable, "OUT_OF_DOMAIN")
        if unusable.get("error_code") != "UNUSABLE_HISTORY":
            raise AssertionError("unusable history was not distinguished from empty history")

        print("phase=protocol-errors", file=sys.stderr, flush=True)
        models = service.request("list_models")
        assert_status(models, "OK")
        unknown = service.request(
            "assess_trawling", {"candidate": rankguess_input, "model_id": "missing-model"}
        )
        assert_status(unknown, "UNAVAILABLE")
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "rankguess_source_line": 1,
                    "pard_source_file": arguments.pard_data.name,
                    "rankguess_elapsed_ms": trawling["elapsed_ms"],
                    "pard_elapsed_ms": derived["elapsed_ms"] if derived else exact["elapsed_ms"],
                    "pard_in_top_k": derived["native"]["in_top_k"] if derived else None,
                    "pard_rank": derived["native"]["best_rank"] if derived else None,
                    "stderr_lines": len(service.stderr_lines),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
