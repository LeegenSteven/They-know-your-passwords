"""Protocol-only fake used by MCP bridge tests; it never logs request values."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config")
    parser.parse_args()

    for raw_line in sys.stdin:
        request = json.loads(raw_line)
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "shutdown":
            response = {"id": request_id, "status": "OK"}
            print(json.dumps(response, separators=(",", ":")), flush=True)
            return 0
        if method == "ping":
            response = {"id": request_id, "status": "OK", "ready": True}
        elif method == "list_models":
            response = {
                "id": request_id,
                "status": "OK",
                "models": [
                    {
                        "model_id": "fake-rankguess",
                        "capabilities": ["ASSESS_TRAWLING"],
                        "ready": True,
                    }
                ],
                "defaults": {"trawling_model_id": "fake-rankguess"},
            }
        elif method == "assess_trawling":
            response = {
                "id": request_id,
                "status": "OK",
                "model_id": "fake-rankguess",
                "metric_type": "GUESS_NUMBER",
                "native": {"guess_number": 42.0, "table_capped": False},
            }
        elif method == "assess_reuse":
            history = params.get("history") or []
            response = {
                "id": request_id,
                "status": "OK",
                "model_id": "fake-pard",
                "metric_type": "REUSE_RANK",
                "native": {
                    "exact_match": bool(history and params.get("candidate") == history[0]),
                    "in_top_k": False,
                    "best_rank": None,
                    "usable_sources": len(history),
                },
            }
        else:
            response = {
                "id": request_id,
                "status": "ERROR",
                "error_code": "UNKNOWN_METHOD",
            }
        print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
