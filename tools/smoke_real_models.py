"""One local stdio smoke. Reports contain no request material or diagnostics."""
import argparse
import json
import os
from pathlib import Path
import queue
import secrets
import string
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="algo-service/config.toml")
    parser.add_argument("--output", default="reports/runtime/model-smoke.json")
    parser.add_argument("--numeric-cases", type=int, default=16)
    parser.add_argument("--python", default=None, help="Independent packaged Python interpreter")
    parser.add_argument("--service", default="algo-service/server.py")
    parser.add_argument("--reuse-budget-ms", type=int, default=3000)
    args = parser.parse_args()
    import sys
    process = subprocess.Popen([args.python or sys.executable, "-B", "-u", args.service, "--config", args.config],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8")
    responses = queue.Queue()
    def read():
        for line in process.stdout:
            try:
                responses.put(json.loads(line))
            except Exception:
                responses.put({"status": "ERROR", "error_code": "INVALID_OUTPUT"})
    threading.Thread(target=read, daemon=True).start()
    def call(request, seconds):
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                response = responses.get(timeout=min(1, max(.01, deadline-time.monotonic())))
                if response.get("id") == request["id"]:
                    return response
            except queue.Empty:
                if process.poll() is not None:
                    return {"status": "UNAVAILABLE", "error_code": "PROCESS_EXITED"}
        return {"status": "TIMEOUT"}
    report = {"checks": []}
    started = time.monotonic()
    try:
        while time.monotonic() - started < 180:
            ready = call({"id": "ready", "method": "ping"}, 5)
            if ready.get("status") not in ("LOADING", "TIMEOUT"):
                break
            time.sleep(.5)
        report["ready"] = ready.get("status")
        report["cold_start_ms"] = round((time.monotonic()-started)*1000)
        if ready.get("status") == "OK":
            identity = call({"id": "identity", "method": "list_models"}, 5)
            report["models"] = identity.get("models", [])
            candidate = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
            for name, method, params in [
                ("trawling", "assess_trawling", {"candidate": candidate}),
                ("exact_reuse", "assess_reuse", {"candidate": candidate, "history": [candidate]}),
                ("beam_reuse", "assess_reuse", {"candidate": candidate, "history": [candidate[:-1] + "!"]}),
            ]:
                t = time.monotonic()
                budget = args.reuse_budget_ms if name == "beam_reuse" else 3000
                result = call({"id": name, "method": method, "timeout_ms": budget, "params": params}, budget / 1000 + 3)
                report["checks"].append({"name": name, "status": result.get("status"),
                    "error_code": result.get("error_code"), "model_version": result.get("model_version"),
                    "elapsed_ms": round((time.monotonic()-t)*1000), "psm": result.get("psm"),
                    "exact": result.get("native", {}).get("exact_match")})
            numeric = {"cases": min(max(args.numeric_cases, 0), 32), "passed": 0, "precision_recovered": 0}
            for index in range(numeric["cases"]):
                value = ''.join(secrets.choice(string.ascii_letters + string.digits + string.punctuation) for _ in range(20))
                result = call({"id": "numeric-%d" % index, "method": "assess_trawling", "timeout_ms": 3000,
                               "params": {"candidate": value}}, 6)
                numeric["passed"] += int(result.get("status") == "OK")
                numeric["precision_recovered"] += int(result.get("native", {}).get("precision_recovered", False))
            report["numerical_regression"] = numeric
    finally:
        process.kill()
        process.wait()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
