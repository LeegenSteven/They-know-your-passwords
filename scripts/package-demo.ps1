[CmdletBinding()]
param(
    [string] $BuildDirectory = 'D:\tmp\TheyKnowYourPasswordsBuild',
    [string] $OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'out\TheyKnowYourPasswords'),
    [switch] $Portable,
    [string] $RuntimeDirectory
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$browserRoot = Join-Path $repositoryRoot 'browser-extension'
$serviceRoot = Join-Path $repositoryRoot 'algo-service'

$requiredBuildFiles = @(
    (Join-Path $BuildDirectory 'src\TheyKnowYourPasswords.exe'),
    (Join-Path $BuildDirectory 'src\proxy\they-know-your-passwords-proxy.exe'),
    (Join-Path $BuildDirectory 'src\cli\they-know-your-passwords-cli.exe')
)
foreach ($requiredPath in $requiredBuildFiles) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Build output not found: $requiredPath"
    }
}

Push-Location $browserRoot
try {
    & node '.\build.js' --skip-translations
    if ($LASTEXITCODE -ne 0) {
        throw "Extension build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

& cmake --install $BuildDirectory --prefix $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "KeePassXC install failed with exit code $LASTEXITCODE"
}

$buildRuntimeDirectory = Join-Path $BuildDirectory 'src'
$vcpkgRuntimeDirectory = Join-Path $BuildDirectory 'vcpkg_installed\x64-windows\bin'
Get-ChildItem -LiteralPath $buildRuntimeDirectory -Filter '*.dll' |
    Copy-Item -Destination $OutputDirectory -Force
Get-ChildItem -LiteralPath $vcpkgRuntimeDirectory -Filter '*.dll' |
    Copy-Item -Destination $OutputDirectory -Force

$bundledService = Join-Path $OutputDirectory 'algo-service'
$bundledAdapters = Join-Path $bundledService 'adapters'
New-Item -ItemType Directory -Path $bundledAdapters -Force | Out-Null
foreach ($fileName in @('server.py', 'registry.py', 'requirements.txt', 'README.md')) {
    Copy-Item -LiteralPath (Join-Path $serviceRoot $fileName) -Destination $bundledService -Force
}
Get-ChildItem -LiteralPath (Join-Path $serviceRoot 'adapters') -Filter '*.py' |
    Copy-Item -Destination $bundledAdapters -Force

$configText = Get-Content -LiteralPath (Join-Path $serviceRoot 'config.toml') -Raw
$rankGuessRoot = '../algorithms/rankguess'
$pardRoot = '../algorithms/pard'
$configText = $configText.Replace('../RankGuess拖网猜测', $rankGuessRoot)
$configText = $configText.Replace('../PARD定向猜测', $pardRoot)
if ($Portable) {
    $configText = $configText.Replace('best_rankguess_guesser_csdn.pth', 'engine-a.bin')
    $configText = $configText.Replace('best_model_02_csdn.pt', 'engine-b.bin')
    $configText = $configText.Replace('device = "auto"', 'device = "cpu"')
}
Set-Content -LiteralPath (Join-Path $bundledService 'config.toml') -Value $configText -Encoding utf8NoBOM

$assetLists = @{
    'rankguess' = @{ Source = 'RankGuess拖网猜测'; Files = @('MC.py', 'best_rankguess_guesser_csdn.pth') }
    'pard' = @{ Source = 'PARD定向猜测'; Files = @('demo6_glca.py', 'eval6_glca.py', 'best_model_02_csdn.pt') }
}
foreach ($assetName in $assetLists.Keys) {
    $destination = Join-Path $OutputDirectory ('algorithms\' + $assetName)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($fileName in $assetLists[$assetName].Files) {
        $outputName = $fileName
        if ($Portable -and $fileName.EndsWith('.pth')) { $outputName = 'engine-a.bin' }
        if ($Portable -and $fileName.EndsWith('.pt')) { $outputName = 'engine-b.bin' }
        Copy-Item -LiteralPath (Join-Path (Join-Path $repositoryRoot $assetLists[$assetName].Source) $fileName) -Destination (Join-Path $destination $outputName) -Force
    }
}
$runtime = Join-Path $repositoryRoot '.runtime'
if ($Portable) { $runtime = Join-Path $repositoryRoot '.tools\portable-runtime' }
if ($RuntimeDirectory) { $runtime = $RuntimeDirectory }
$pythonRelative = if ($Portable) { 'python.exe' } else { 'Scripts\python.exe' }
if (-not (Test-Path -LiteralPath (Join-Path $runtime $pythonRelative))) { throw 'Build the selected runtime first.' }
$bundledRuntime = Join-Path $OutputDirectory 'runtime'
New-Item -ItemType Directory -Path $bundledRuntime -Force | Out-Null
Get-ChildItem -LiteralPath $runtime | Copy-Item -Destination $bundledRuntime -Recurse -Force
if ($Portable) {
    $redistRoot = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Redist\MSVC'
    $crt = Get-ChildItem -LiteralPath $redistRoot -Directory | Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\Microsoft.VC143.CRT' } |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $crt) { throw 'MSVC redistributable DLLs are required for the portable release.' }
    Get-ChildItem -LiteralPath $crt -Filter '*.dll' | Copy-Item -Destination $OutputDirectory -Force
}
# The reference cache contains only probabilities, counts and configuration metadata.
$cache = Join-Path $serviceRoot 'mc_cache'
if (Test-Path -LiteralPath $cache) {
    $bundledCache = Join-Path $bundledService 'mc_cache'
    New-Item -ItemType Directory -Path $bundledCache -Force | Out-Null
    Get-ChildItem -LiteralPath $cache -Filter '*.npz' | Copy-Item -Destination $bundledCache -Force
}

$extensionZip = Get-ChildItem -LiteralPath $browserRoot -Filter 'they-know-your-passwords_*_chromium.zip' |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if (-not $extensionZip) {
    throw 'Chromium extension package was not generated.'
}
$extensionDirectory = Join-Path $OutputDirectory 'extension-chromium'
New-Item -ItemType Directory -Path $extensionDirectory -Force | Out-Null
Expand-Archive -LiteralPath $extensionZip.FullName -DestinationPath $extensionDirectory -Force

$documentationDirectory = Join-Path $OutputDirectory 'docs'
New-Item -ItemType Directory -Path $documentationDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'README.md') -Destination $OutputDirectory -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'docs\windows-demo.md') -Destination $documentationDirectory -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'docs\browser-contract.md') -Destination $documentationDirectory -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'docs\algorithm-contract.md') -Destination $documentationDirectory -Force
foreach ($document in @('validation-report.md', 'model-provenance.md', 'threat-model.md', 'distribution.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot ('docs\' + $document)))) { continue }
    Copy-Item -LiteralPath (Join-Path $repositoryRoot ('docs\' + $document)) -Destination $documentationDirectory -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start-demo.ps1') -Destination $OutputDirectory -Force
foreach ($name in @('register-browsers.ps1', 'check-environment.ps1', 'window-display.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $OutputDirectory -Force
}
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'docs\first-use.md') -Destination $OutputDirectory -Force

foreach ($name in @('Launch.cmd', '启动软件.cmd')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Launch.cmd') -Destination (Join-Path $OutputDirectory $name) -Force
}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'CheckEnvironment.cmd') -Destination $OutputDirectory -Force
foreach ($name in @('Launch-Remote.cmd','远程启动.cmd')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Launch-Remote.cmd') -Destination (Join-Path $OutputDirectory $name) -Force
}
foreach ($name in @('Setup-Remote.cmd','远程首次配置.cmd')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Setup-Remote.cmd') -Destination (Join-Path $OutputDirectory $name) -Force
}
if (-not $Portable) {
    $shellObject = New-Object -ComObject WScript.Shell
    $shortcut = $shellObject.CreateShortcut((Join-Path $OutputDirectory 'They know your passwords.lnk'))
    $shortcut.TargetPath = Join-Path $OutputDirectory '启动软件.cmd'
    $shortcut.WorkingDirectory = $OutputDirectory
    $shortcut.IconLocation = Join-Path $OutputDirectory 'TheyKnowYourPasswords.exe'
    $shortcut.Save()
}

foreach ($name in @('Setup.cmd', '首次配置.cmd')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Setup.cmd') -Destination (Join-Path $OutputDirectory $name) -Force
}
$licenses = Join-Path $OutputDirectory 'licenses'
New-Item -ItemType Directory -Path $licenses -Force | Out-Null
Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'desktop-app') -Filter 'LICENSE*' | Copy-Item -Destination $licenses -Force
Copy-Item -LiteralPath (Join-Path $repositoryRoot 'desktop-app\COPYING') -Destination $licenses -Force
Copy-Item -LiteralPath (Join-Path $browserRoot 'LICENSE') -Destination (Join-Path $licenses 'browser-extension.LICENSE') -Force
$dependencyLicenses = Join-Path $BuildDirectory 'vcpkg_installed\x64-windows\share'
Get-ChildItem -LiteralPath $dependencyLicenses -Filter copyright -Recurse | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $licenses ($_.Directory.Name + '.copyright')) -Force
}
& (Join-Path $PSScriptRoot 'write-package-manifest.ps1') -PackageDirectory $OutputDirectory | Out-Null

[pscustomobject]@{
    OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
    DesktopApp = (Join-Path $OutputDirectory 'TheyKnowYourPasswords.exe')
    Extension = $extensionDirectory
    StartScript = (Join-Path $OutputDirectory 'start-demo.ps1')
}
