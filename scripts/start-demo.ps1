[CmdletBinding()]
param([string] $PythonExecutable, [string] $KeePassExecutable, [string] $ServiceScript,
      [string] $ServiceConfig, [string] $ConfigDirectory, [string[]] $DatabaseFiles = @(),
      [switch] $AllowScreenCapture,
      [ValidateRange(1, 60)][int] $LaunchTimeoutSeconds = 15)
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
if ($AllowScreenCapture) { $arguments += '--allow-screencapture' }
$arguments += $DatabaseFiles | ForEach-Object { '"' + $_ + '"' }
function Find-ExistingDesktop {
    $lockUser = $env:USERNAME
    if (-not $lockUser) { $lockUser = $env:USER }
    if (-not $lockUser) { $lockUser = '' }
    $lockUser = ($lockUser.Trim() -replace '[<>:"/\\|?*]', '_') -replace '[.\s]+$', ''
    $lockName = 'keepassxc'
    if ($lockUser) { $lockName += '-' + $lockUser }
    $lockPath = Join-Path ([IO.Path]::GetTempPath()) ($lockName + '.lock')
    if (-not (Test-Path -LiteralPath $lockPath)) { return }
    $instanceId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $lockPath -TotalCount 1), [ref]$instanceId)) { return }
    $instance = Get-Process -Id $instanceId -ErrorAction SilentlyContinue
    if (-not $instance -or $instance.SessionId -ne (Get-Process -Id $PID).SessionId -or
        $instance.ProcessName -ne [IO.Path]::GetFileNameWithoutExtension($KeePassExecutable)) { return }
    return $instance
}
. (Join-Path $PSScriptRoot 'window-display.ps1')
# The legacy GUI cannot change capture protection while already running.
# Only normal close is permitted: unsaved-data prompts may refuse restart.
$previous = Find-ExistingDesktop
if ($AllowScreenCapture -and $previous -and -not [TkypDesktopWindow]::CaptureAllowed($previous.Id)) {
    Write-Output 'Remote display requested. Closing the protected instance normally before restarting.'
    [TkypDesktopWindow]::Restore($previous.Id)
    if (-not $previous.CloseMainWindow() -or -not $previous.WaitForExit(3000)) {
        throw 'The existing instance has not closed. Save pending changes and exit it normally, then run Launch-Remote.cmd. No process was forcibly stopped.'
    }
}
# This is the user's interactive desktop, not the background algorithm worker.
# SW_HIDE suppresses Qt's first native show even when Qt considers it visible;
# subsequent single-instance activations then also fail to display that window.
$desktop = Start-Process -FilePath $KeePassExecutable -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Normal -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds($LaunchTimeoutSeconds)
$readyChecks = 0
do {
    $desktop.Refresh()
    if ($desktop.HasExited) {
        if ($desktop.ExitCode -ne 0) { throw "Desktop launch failed (exit code $($desktop.ExitCode)). Run check-environment.ps1." }
        # The second invocation exits after asking the existing instance to show.
        $existing = Find-ExistingDesktop
        if ($existing) {
            [TkypDesktopWindow]::Restore($existing.Id)
            if ([TkypDesktopWindow]::Ready($existing.Id, $AllowScreenCapture.IsPresent)) {
                $readyChecks++
                if ($readyChecks -ge 3) { Write-Output 'Desktop window is shown (existing instance).'; return }
            } else {
                $readyChecks = 0
            }
        }
    } else {
        [TkypDesktopWindow]::Restore($desktop.Id)
        if ([TkypDesktopWindow]::Ready($desktop.Id, $AllowScreenCapture.IsPresent)) {
            $readyChecks++
            if ($readyChecks -ge 3) {
                if ($AllowScreenCapture) { Write-Output 'Desktop window is shown. Remote viewing is enabled for this session.' }
                else { Write-Output 'Desktop window is shown. Screen sharing is blocked by default; use Launch-Remote.cmd for remote viewing.' }
                return
            }
        } else {
            $readyChecks = 0
        }
    }
    Start-Sleep -Milliseconds 200
} while ([DateTime]::UtcNow -lt $deadline)
throw 'The desktop window is not displayed on the current screen within the startup timeout. Run CheckEnvironment.cmd. For remote/shared viewing, use Launch-Remote.cmd.'
