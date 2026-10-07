[CmdletBinding()]
param([string] $PackageRoot)
$ErrorActionPreference = 'Stop'
if (-not $PackageRoot) { $PackageRoot = $PSScriptRoot }
$pythonFile = 'runtime\python.exe'
$rankModel = 'algorithms\rankguess\engine-a.bin'
$pardModel = 'algorithms\pard\engine-b.bin'
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $pythonFile))) { $pythonFile = 'runtime\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $rankModel))) { $rankModel = 'algorithms\rankguess\best_rankguess_guesser_csdn.pth' }
if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot $pardModel))) { $pardModel = 'algorithms\pard\best_model_02_csdn.pt' }
$result = [ordered]@{ package = $PackageRoot; files = @(); nativeMessaging = @(); cli = 'UNKNOWN'; python = 'UNKNOWN' }
foreach ($relative in @('TheyKnowYourPasswords.exe', 'they-know-your-passwords-cli.exe',
    'they-know-your-passwords-proxy.exe', 'platforms\qwindows.dll', $pythonFile,
    'algo-service\config.toml', 'extension-chromium\manifest.json',
    $rankModel, $pardModel)) {
    $result.files += @{ file = $relative; exists = (Test-Path -LiteralPath (Join-Path $PackageRoot $relative)) }
}
foreach ($browser in @('Google\Chrome', 'Microsoft\Edge')) {
    $key = 'HKCU:\Software\' + $browser + '\NativeMessagingHosts\org.keepassxc.keepassxc_browser'
    $result.nativeMessaging += @{ browser = $browser; registered = (Test-Path -LiteralPath $key) }
}
$env:PATH = $PackageRoot + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
$env:QT_PLUGIN_PATH = $PackageRoot
$env:QT_QPA_PLATFORM_PLUGIN_PATH = Join-Path $PackageRoot 'platforms'
$version = & (Join-Path $PackageRoot 'they-know-your-passwords-cli.exe') --version
$result.cli = if ($LASTEXITCODE -eq 0) { 'OK' } else { 'ERROR' }
$python = & (Join-Path $PackageRoot $pythonFile) -B -c 'import json,torch,tomli,torch_geometric; print(json.dumps(dict(torch=torch.__version__,pyg=torch_geometric.__version__,cuda=torch.cuda.is_available())))'
$result.python = if ($LASTEXITCODE -eq 0) { $python | ConvertFrom-Json } else { 'ERROR' }
$result | ConvertTo-Json -Depth 5
