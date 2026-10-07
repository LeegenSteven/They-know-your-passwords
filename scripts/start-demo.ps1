[CmdletBinding()]
param([string] $PythonExecutable, [string] $KeePassExecutable, [string] $ServiceScript,
      [string] $ServiceConfig, [string] $ConfigDirectory, [string[]] $DatabaseFiles = @(),
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
$arguments += $DatabaseFiles | ForEach-Object { '"' + $_ + '"' }
function Get-ExistingDesktop {
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
    if ($instance.MainWindowHandle -eq [IntPtr]::Zero) {
        # Recover a v0.2.0 SW_HIDE window. Only inspect top-level Qt windows of
        # the PID in this user's single-instance lock; never read window text.
        if (-not ('TkypLaunchWindow' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class TkypLaunchWindow {
    delegate bool Callback(IntPtr window, IntPtr state);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr state);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder name, int size);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    struct Rect { public int Left, Top, Right, Bottom; }
    public static void Restore(int process) {
        EnumWindows(delegate(IntPtr window, IntPtr state) {
            uint owner; GetWindowThreadProcessId(window, out owner);
            if (owner != process || GetWindow(window, 4) != IntPtr.Zero) return true;
            var name = new StringBuilder(256); GetClassName(window, name, name.Capacity);
            Rect rect; GetWindowRect(window, out rect);
            if (name.ToString().EndsWith("QWindowIcon") && rect.Right-rect.Left >= 200 && rect.Bottom-rect.Top >= 150) {
                ShowWindow(window, 9); SetForegroundWindow(window);
            }
            return true;
        }, IntPtr.Zero);
    }
}
'@
        }
        [TkypLaunchWindow]::Restore($instance.Id)
        $instance.Refresh()
    }
    if ($instance.MainWindowHandle -ne [IntPtr]::Zero) { return $instance }
}
# This is the user's interactive desktop, not the background algorithm worker.
# SW_HIDE suppresses Qt's first native show even when Qt considers it visible;
# subsequent single-instance activations then also fail to display that window.
$desktop = Start-Process -FilePath $KeePassExecutable -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Normal -PassThru
$deadline = [DateTime]::UtcNow.AddSeconds($LaunchTimeoutSeconds)
do {
    $desktop.Refresh()
    if ($desktop.HasExited) {
        if ($desktop.ExitCode -ne 0) { throw "Desktop launch failed (exit code $($desktop.ExitCode)). Run check-environment.ps1." }
        # The second invocation exits after asking the existing instance to show.
        $existing = Get-ExistingDesktop
        if ($existing) { Write-Output 'Desktop window is open (existing instance).'; return }
    } elseif ($desktop.MainWindowHandle -ne [IntPtr]::Zero) {
        Write-Output 'Desktop window is open.'
        return
    }
    Start-Sleep -Milliseconds 200
} while ([DateTime]::UtcNow -lt $deadline)
throw 'No desktop window appeared within the startup timeout. Run check-environment.ps1; if an old instance is hidden, open it from the system tray or exit it there and retry.'
