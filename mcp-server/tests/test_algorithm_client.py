from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from typ_mcp.algorithm_client import AlgorithmServiceClient


class AlgorithmServiceClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        config_path = Path(self.temporary_directory.name) / "config.toml"
        config_path.write_text("[service]\n", encoding="utf-8")
        fake_service = Path(__file__).with_name("fake_algorithm_service.py")
        self.client = AlgorithmServiceClient(
            Path(sys.executable),
            fake_service,
            config_path,
            startup_timeout_ms=2_000,
        )

    async def asyncTearDown(self) -> None:
        await self.client.close()
        self.temporary_directory.cleanup()

    async def test_round_trip_and_sanitized_result(self) -> None:
        synthetic_input = "".join(chr(value) for value in (68, 101, 109, 111, 45, 52, 50))
        ready = await self.client.wait_until_ready()
        self.assertEqual("OK", ready["status"])

        result = await self.client.call(
            "assess_trawling",
            {"candidate": synthetic_input},
            timeout_ms=1_000,
        )

        self.assertEqual("OK", result["status"])
        self.assertNotIn(synthetic_input, json.dumps(result))
        self.assertTrue(self.client.running)

    async def test_model_listing(self) -> None:
        result = await self.client.call("list_models", timeout_ms=1_000)
        self.assertEqual("fake-rankguess", result["models"][0]["model_id"])
