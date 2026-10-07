[CmdletBinding()]
param([string] $PythonExecutable, [string] $KeePassExecutable, [string] $ServiceScript,
      [string] $ServiceConfig, [string] $ConfigDirectory, [string[]] $DatabaseFiles = @())
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $root 'TheyKnowYourPasswords.exe'))) {
    $root = Join-Path (Split-Path -Parent $root) 'out\TheyKnowYourPasswords'
}
if (-not $KeePassExecutable) { $KeePassExecutable = Join-Path $root 'TheyKnowYourPasswords.exe' }
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $root 'runtime\python.exe'
    if (-not (Test-Path -LiteralPath $PythonExecutable)) { $PythonExecutable = Join-Path $root 'runtime\Scripts\python.exe' }
}
if (-not $ServiceScript) { $ServiceScript = Join-Path $root 'algo-service\server.py' }
if (-not $ServiceConfig) { $ServiceConfig = Join-Path $root 'algo-service\config.toml' }
if (-not $ConfigDirectory) { $ConfigDirectory = Join-Path $root 'settings' }
foreach ($required in @($KeePassExecutable, $PythonExecutable, $ServiceScript, $ServiceConfig)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required file is missing: $required" }
}
New-Item -ItemType Directory -Path $ConfigDirectory -Force | Out-Null
$settings = Join-Path $ConfigDirectory 'settings.ini'
$local = Join-Path $ConfigDirectory 'local.ini'
if (-not (Test-Path -LiteralPath $settings)) {
    [IO.File]::WriteAllText($settings, "[General]`nAutoSaveAfterEveryChange=false`n[GUI]`nLanguage=zh_CN`n[Browser]`nEnabled=true`nUpdateBinaryPath=false`n", [Text.UTF8Encoding]::new($false))
}
if (-not (Test-Path -LiteralPath $local)) {
    [IO.File]::WriteAllText($local, "[Browser]`nRiskAssessmentEnabled=true`n", [Text.UTF8Encoding]::new($false))
}
$env:KEEPASSXC_RISK_PYTHON = $PythonExecutable
$env:KEEPASSXC_RISK_SERVICE = $ServiceScript
$env:KEEPASSXC_RISK_CONFIG = $ServiceConfig
$env:PATH = $root + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
$env:QT_PLUGIN_PATH = $root
$env:QT_QPA_PLATFORM_PLUGIN_PATH = Join-Path $root 'platforms'
$arguments = @('--config', ('"' + $settings + '"'), '--localconfig', ('"' + $local + '"'))
$arguments += $DatabaseFiles | ForEach-Object { '"' + $_ + '"' }
Start-Process -FilePath $KeePassExecutable -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden
