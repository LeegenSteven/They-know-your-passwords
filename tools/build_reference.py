"""Offline release preparation; never invokes an algorithm main or reads inputs."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    config = Path(args.config).resolve()
    sys.path.insert(0, str(config.parent))
    from server import JsonLineServer
    started = time.monotonic()
    # Adapter diagnostics do not leave memory; only public identities are kept.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        service = JsonLineServer(config, warmup=False)
        service._registry.load_all()
    report = {"ready": "OK", "elapsed_ms": round((time.monotonic() - started) * 1000),
              "models": service._registry.list_models()["models"], "input_cases": 0}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ready": report["ready"], "elapsed_ms": report["elapsed_ms"], "input_cases": 0}))


if __name__ == "__main__":
    main()
