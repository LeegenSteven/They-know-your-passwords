[CmdletBinding()]
param([string] $PackageDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'out\TheyKnowYourPasswords'))
$ErrorActionPreference = 'Stop'
$packagePath = (Resolve-Path -LiteralPath $PackageDirectory).Path
$portable = Test-Path -LiteralPath (Join-Path $packagePath 'runtime\python.exe')
$rankModel = if ($portable) { 'algorithms\rankguess\engine-a.bin' } else { 'algorithms\rankguess\best_rankguess_guesser_csdn.pth' }
$pardModel = if ($portable) { 'algorithms\pard\engine-b.bin' } else { 'algorithms\pard\best_model_02_csdn.pt' }
$files = @('Launch.cmd', '启动软件.cmd', 'Setup.cmd', '首次配置.cmd', 'CheckEnvironment.cmd', 'start-demo.ps1',
    'TheyKnowYourPasswords.exe', 'they-know-your-passwords-proxy.exe', 'they-know-your-passwords-cli.exe',
    'algo-service\server.py', 'algo-service\config.toml', 'algo-service\adapters\rankguess.py',
    'algo-service\adapters\pard.py', 'algo-service\adapters\psm.py', 'extension-chromium\manifest.json',
    'algorithms\rankguess\MC.py', $rankModel,
    'algorithms\pard\demo6_glca.py', 'algorithms\pard\eval6_glca.py', $pardModel)
if ($portable) { $files += @('runtime\python.exe', 'runtime\python312.dll', 'runtime\python312._pth', 'runtime\build-artifacts.json') }
$identities = foreach ($file in $files) {
    $path = Join-Path $packagePath $file
    [ordered]@{ file = $file; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
}
$manifest = [ordered]@{
    schema = 1; createdUtc = [DateTime]::UtcNow.ToString('o'); scope = $(if ($portable) { 'windows-x64-portable-cpu' } else { 'local-machine' }); calibrated = $false
    chromiumExtensionId = 'ijlckofhohjbbifcfhpiglkmfndaaeol'; files = @($identities)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $packagePath 'package-manifest.json') -Encoding utf8NoBOM
[pscustomobject]@{ Files = $identities.Count; Manifest = (Join-Path $packagePath 'package-manifest.json') }
