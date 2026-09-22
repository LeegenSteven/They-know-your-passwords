from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


class StdioIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_host_can_launch_and_call_demo_server(self) -> None:
        mcp_root = Path(__file__).resolve().parents[1]
        fake_service = Path(__file__).with_name("fake_algorithm_service.py")
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.toml"
            config_path.write_text("[service]\n", encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "TYP_ALGORITHM_PYTHON": sys.executable,
                    "TYP_ALGORITHM_SERVER": str(fake_service),
                    "TYP_ALGORITHM_CONFIG": str(config_path),
                    "TYP_MCP_STARTUP_TIMEOUT_MS": "2000",
                }
            )
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[str(mcp_root / "server.py")],
                env=environment,
                cwd=mcp_root,
            )
            synthetic_input = "".join(chr(value) for value in (83, 116, 100, 105, 111, 45, 52, 50))
            async with Client(parameters, read_timeout_seconds=5.0) as client:
                tools = await client.list_tools()
                result = await client.call_tool(
                    "assess_demo_password",
                    {"candidate": synthetic_input, "mode": "trawling", "timeout_ms": 1000},
                )

        self.assertIn("assess_demo_password", {tool.name for tool in tools.tools})
        self.assertEqual("OK", result.structured_content["status"])
        self.assertNotIn(synthetic_input, json.dumps(result.structured_content))
