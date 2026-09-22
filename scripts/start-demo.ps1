[CmdletBinding()]
param(
    [string] $PythonExecutable = 'D:\Anaconda3\envs\pytorch_cuda\python.exe',
    [string] $KeePassExecutable,
    [string] $ServiceScript,
    [string] $ServiceConfig
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = $PSScriptRoot
$repositoryRoot = Split-Path -Parent $scriptDirectory

if (-not $KeePassExecutable) {
    $bundledKeePass = Join-Path $scriptDirectory 'KeePassXC.exe'
    $KeePassExecutable = if (Test-Path -LiteralPath $bundledKeePass) {
        $bundledKeePass
    } else {
        'D:\tmp\TheyKnowDemo\KeePassXC.exe'
    }
}

if (-not $ServiceScript) {
    $bundledService = Join-Path $scriptDirectory 'algo-service\server.py'
    $repositoryService = Join-Path $repositoryRoot 'algo-service\server.py'
    $ServiceScript = if (Test-Path -LiteralPath $bundledService) {
        $bundledService
    } else {
        $repositoryService
    }
}

if (-not $ServiceConfig) {
    $ServiceConfig = Join-Path (Split-Path -Parent $ServiceScript) 'config.toml'
}

foreach ($requiredPath in @($PythonExecutable, $KeePassExecutable, $ServiceScript, $ServiceConfig)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file not found: $requiredPath"
    }
}

$env:KEEPASSXC_RISK_PYTHON = (Resolve-Path -LiteralPath $PythonExecutable).Path
$env:KEEPASSXC_RISK_SERVICE = (Resolve-Path -LiteralPath $ServiceScript).Path
$env:KEEPASSXC_RISK_CONFIG = (Resolve-Path -LiteralPath $ServiceConfig).Path

& $KeePassExecutable
exit $LASTEXITCODE
