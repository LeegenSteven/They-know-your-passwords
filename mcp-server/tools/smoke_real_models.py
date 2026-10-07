"""Actual MCP stdio/model smoke; outputs only non-secret result metadata."""
import asyncio
import json
import os
import secrets
import string
import sys
import time
from pathlib import Path
from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main():
    root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(command=sys.executable, args=[str(root / 'server.py')],
                                       cwd=root, env=os.environ.copy())
    started = time.monotonic()
    candidate = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))
    async with Client(parameters, read_timeout_seconds=210) as client:
        tools = await client.list_tools()
        result = await client.call_tool('assess_demo_password', {'candidate': candidate, 'mode': 'trawling'})
        response = result.structured_content
        summary = {'status': response.get('status'), 'model_id': response.get('model_id'),
                   'error_code': response.get('error_code'),
                   'calibrated': response.get('calibrated'), 'demo_only': response.get('demo_only'),
                   'tools_count': len(tools.tools), 'input_not_echoed': candidate not in json.dumps(response),
                   'elapsed_ms': round((time.monotonic() - started) * 1000)}
        output = root.parent / 'reports/runtime/mcp-model-smoke.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary))
        return 0 if summary['status'] == 'OK' and summary['input_not_echoed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'status': 'ERROR', 'error_type': type(error).__name__}))
        sys.exit(1)
