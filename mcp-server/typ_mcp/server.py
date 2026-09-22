"""MCP tools for synthetic password-risk demonstrations."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any, AsyncIterator, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .algorithm_client import AlgorithmServiceClient, AlgorithmServiceError


JsonObject = dict[str, Any]
Mode = Literal["auto", "trawling", "reuse"]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _bridge_error(error: AlgorithmServiceError) -> JsonObject:
    return {
        "status": "UNAVAILABLE",
        "ready": False,
        "error_code": error.error_code,
        "detail": "local algorithm service is unavailable",
        "demo_only": True,
    }


def _classify_demo_result(route: str, response: JsonObject) -> tuple[str, str]:
    if response.get("status") != "OK":
        return "UNKNOWN", str(response.get("error_code", "ALGORITHM_RESULT_UNAVAILABLE"))
    native = response.get("native")
    if route == "reuse" and isinstance(native, dict) and native.get("exact_match") is True:
        return "HIGH", "EXACT_REUSE"
    return "UNKNOWN", "UNCALIBRATED"


def create_server(client: AlgorithmServiceClient | Any | None = None) -> MCPServer:
    bridge = client or AlgorithmServiceClient.from_environment()

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await bridge.close()

    server = MCPServer(
        "they-know-your-passwords",
        title="They know your passwords (Demo)",
        description="Local demo tools for RankGuess and PARD password-risk inference.",
        instructions=(
            "Use only synthetic or test strings. Never send a real password, vault entry, master key, "
            "username, or recovery secret to this demo server. Results are uncalibrated unless the "
            "response explicitly says otherwise."
        ),
        version=__version__,
        lifespan=lifespan,
        log_level="WARNING",
    )

    @server.tool(
        name="get_security_status",
        title="Get local risk service status",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def get_security_status(
        wait_for_ready: Annotated[
            bool,
            Field(description="Wait for local model warmup before returning."),
        ] = False,
        wait_timeout_ms: Annotated[
            int,
            Field(ge=1_000, le=600_000, description="Maximum local warmup wait in milliseconds."),
        ] = 10_000,
    ) -> JsonObject:
        """Check the local demo algorithm service without reading any password vault."""
        configuration = bridge.configuration_status()
        if not all(configuration.values()):
            return {
                "status": "UNAVAILABLE",
                "ready": False,
                "error_code": "MCP_CONFIGURATION_INCOMPLETE",
                "configuration": configuration,
                "demo_only": True,
            }
        try:
            response = (
                await bridge.wait_until_ready(wait_timeout_ms)
                if wait_for_ready
                else await bridge.call("ping", timeout_ms=2_000)
            )
        except AlgorithmServiceError as error:
            return _bridge_error(error)
        return {
            **response,
            "configuration": configuration,
            "process_running": bridge.running,
            "demo_only": True,
        }

    @server.tool(
        name="list_risk_models",
        title="List local password-risk models",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def list_risk_models(
        wait_for_ready: Annotated[
            bool,
            Field(description="Wait for local model warmup before listing model readiness."),
        ] = False,
        wait_timeout_ms: Annotated[
            int,
            Field(ge=1_000, le=600_000, description="Maximum local warmup wait in milliseconds."),
        ] = 10_000,
    ) -> JsonObject:
        """List registered models and capabilities; returns no model weights or local paths."""
        try:
            if wait_for_ready:
                ready = await bridge.wait_until_ready(wait_timeout_ms)
                if ready.get("status") != "OK":
                    return {**ready, "demo_only": True}
            response = await bridge.call("list_models", timeout_ms=2_000)
        except AlgorithmServiceError as error:
            return _bridge_error(error)
        return {**response, "demo_only": True}

    @server.tool(
        name="assess_demo_password",
        title="Assess a synthetic password",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def assess_demo_password(
        candidate: Annotated[
            str,
            Field(
                min_length=1,
                max_length=256,
                description="Synthetic test string only. Never provide a real password or secret.",
            ),
        ],
        history: Annotated[
            list[str] | None,
            Field(
                max_length=5,
                description="Optional synthetic prior strings. Never provide real password history.",
            ),
        ] = None,
        mode: Annotated[
            Mode,
            Field(description="auto selects reuse when history is present, otherwise trawling."),
        ] = "auto",
        timeout_ms: Annotated[
            int,
            Field(ge=100, le=120_000, description="Inference timeout after models are ready."),
        ] = 3_000,
    ) -> JsonObject:
        """Assess synthetic input with RankGuess or PARD; input text is never echoed in the result."""
        synthetic_history = history or []
        if any(len(value) > 256 for value in synthetic_history):
            return {
                "status": "ERROR",
                "error_code": "INPUT_TOO_LARGE",
                "risk_level": "UNKNOWN",
                "calibrated": False,
                "demo_only": True,
            }
        route = "reuse" if mode == "reuse" or (mode == "auto" and synthetic_history) else "trawling"
        try:
            ready = await bridge.wait_until_ready(bridge.startup_timeout_ms)
            if ready.get("status") != "OK":
                return {
                    **ready,
                    "route": route,
                    "risk_level": "UNKNOWN",
                    "calibrated": False,
                    "demo_only": True,
                }
            params: JsonObject = {"candidate": candidate}
            method = "assess_trawling"
            if route == "reuse":
                method = "assess_reuse"
                params["history"] = synthetic_history
            response = await bridge.call(method, params, timeout_ms=timeout_ms)
        except AlgorithmServiceError as error:
            return {
                **_bridge_error(error),
                "route": route,
                "risk_level": "UNKNOWN",
                "calibrated": False,
            }
        risk_level, reason = _classify_demo_result(route, response)
        return {
            **response,
            "route": route,
            "risk_level": risk_level,
            "reason": reason,
            "calibrated": False,
            "demo_only": True,
        }

    return server


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
