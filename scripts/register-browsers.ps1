[CmdletBinding()]
param([string] $PackageRoot)
$ErrorActionPreference = 'Stop'
if (-not $PackageRoot) { $PackageRoot = $PSScriptRoot }
$proxy = Join-Path $PackageRoot 'they-know-your-passwords-proxy.exe'
if (-not (Test-Path -LiteralPath $proxy)) { throw 'Package proxy is missing.' }
$nativeDir = Join-Path $PackageRoot 'native-messaging'
New-Item -ItemType Directory -Path $nativeDir -Force | Out-Null
$manifestPath = Join-Path $nativeDir 'org.keepassxc.keepassxc_browser.json'
$manifest = @{
    name = 'org.keepassxc.keepassxc_browser'
    description = 'They know your passwords local browser integration'
    path = $proxy
    type = 'stdio'
    allowed_origins = @('chrome-extension://ijlckofhohjbbifcfhpiglkmfndaaeol/')
}
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
foreach ($browser in @('Google\Chrome', 'Microsoft\Edge')) {
    $key = 'HKCU:\Software\' + $browser + '\NativeMessagingHosts\org.keepassxc.keepassxc_browser'
    New-Item -Path $key -Force | Out-Null
    Set-Item -LiteralPath $key -Value $manifestPath
}
Write-Output 'Native Messaging: Chrome/Edge registered for fixed extension ID.'
