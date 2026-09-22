[CmdletBinding()]
param(
    [string] $BuildDirectory = 'D:\tmp\TheyKnowYourPasswordsBuild',
    [string] $OutputDirectory = 'D:\tmp\TheyKnowYourPasswordsPackage'
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
$rankGuessRoot = (Join-Path $repositoryRoot 'RankGuess拖网猜测').Replace('\', '/')
$pardRoot = (Join-Path $repositoryRoot 'PARD定向猜测').Replace('\', '/')
$configText = $configText.Replace('../RankGuess拖网猜测', $rankGuessRoot)
$configText = $configText.Replace('../PARD定向猜测', $pardRoot)
Set-Content -LiteralPath (Join-Path $bundledService 'config.toml') -Value $configText -Encoding utf8NoBOM

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
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start-demo.ps1') -Destination $OutputDirectory -Force

[pscustomobject]@{
    OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
    DesktopApp = (Join-Path $OutputDirectory 'TheyKnowYourPasswords.exe')
    Extension = $extensionDirectory
    StartScript = (Join-Path $OutputDirectory 'start-demo.ps1')
}
