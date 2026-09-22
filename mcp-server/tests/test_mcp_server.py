from __future__ import annotations

import json
import unittest
from typing import Any

from mcp import Client

from typ_mcp.server import create_server


class StubBridge:
    startup_timeout_ms = 1_000

    def __init__(self) -> None:
        self.running = True
        self.closed = False

    def configuration_status(self) -> dict[str, bool]:
        return {
            "python_configured": True,
            "service_configured": True,
            "config_configured": True,
        }

    async def wait_until_ready(self, timeout_ms: int) -> dict[str, Any]:
        return {"status": "OK", "ready": True}

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_ms: int,
    ) -> dict[str, Any]:
        if method == "ping":
            return {"status": "OK", "ready": True}
        if method == "list_models":
            return {
                "status": "OK",
                "models": [{"model_id": "stub", "capabilities": ["ASSESS_TRAWLING"], "ready": True}],
                "defaults": {"trawling_model_id": "stub"},
            }
        if method == "assess_trawling":
            return {
                "status": "OK",
                "model_id": "stub",
                "metric_type": "GUESS_NUMBER",
                "native": {"guess_number": 12.0},
            }
        if method == "assess_reuse":
            values = params or {}
            history = values.get("history") or []
            return {
                "status": "OK",
                "model_id": "stub",
                "metric_type": "REUSE_RANK",
                "native": {"exact_match": bool(history and values.get("candidate") == history[0])},
            }
        raise AssertionError("unexpected method")

    async def close(self) -> None:
        self.closed = True


class McpServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_are_discoverable(self) -> None:
        bridge = StubBridge()
        server = create_server(bridge)
        async with Client(server) as client:
            page = await client.list_tools()
            names = {tool.name for tool in page.tools}
        self.assertEqual(
            {"get_security_status", "list_risk_models", "assess_demo_password"},
            names,
        )
        self.assertTrue(bridge.closed)

    async def test_assessment_does_not_echo_input(self) -> None:
        bridge = StubBridge()
        server = create_server(bridge)
        synthetic_input = "".join(chr(value) for value in (84, 101, 115, 116, 45, 52, 50))
        async with Client(server) as client:
            result = await client.call_tool(
                "assess_demo_password",
                {"candidate": synthetic_input, "mode": "trawling"},
            )
        self.assertIsNotNone(result.structured_content)
        serialized = json.dumps(result.structured_content)
        self.assertNotIn(synthetic_input, serialized)
        self.assertEqual("UNKNOWN", result.structured_content["risk_level"])
        self.assertEqual("UNCALIBRATED", result.structured_content["reason"])

    async def test_exact_synthetic_reuse_is_high(self) -> None:
        bridge = StubBridge()
        server = create_server(bridge)
        synthetic_input = "".join(chr(value) for value in (82, 101, 117, 115, 101, 45, 52, 50))
        async with Client(server) as client:
            result = await client.call_tool(
                "assess_demo_password",
                {
                    "candidate": synthetic_input,
                    "history": [synthetic_input],
                    "mode": "auto",
                },
            )
        self.assertEqual("HIGH", result.structured_content["risk_level"])
        self.assertEqual("EXACT_REUSE", result.structured_content["reason"])
        self.assertFalse(result.structured_content["calibrated"])
