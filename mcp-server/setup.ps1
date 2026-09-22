param(
    [string]$PythonPath = "D:\Anaconda3\python.exe"
)

$ErrorActionPreference = "Stop"
$McpRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $McpRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python 3.10 or newer was not found at the configured path."
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $PythonPath -m venv (Join-Path $McpRoot ".venv")
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $McpRoot "requirements.txt")
& $VenvPython -c "import mcp; print('MCP environment ready')"
