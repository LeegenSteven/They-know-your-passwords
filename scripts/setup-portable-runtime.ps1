[CmdletBinding()]
param(
    [string] $BuildPython = 'D:\Anaconda3\envs\pytorch_cuda\python.exe',
    [string] $RuntimeDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) '.tools\portable-runtime'),
    [string] $WheelDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) '.tools\portable-wheels')
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $taskRoot '.tools\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP,$RuntimeDirectory,$WheelDirectory -Force | Out-Null
$embedZip = Join-Path $taskRoot '.tools\python-3.12.10-embed-amd64.zip'
if (-not (Test-Path -LiteralPath $embedZip)) {
    Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $embedZip
}
Expand-Archive -LiteralPath $embedZip -DestinationPath $RuntimeDirectory -Force
$pipCommon = @('-m','pip','download','--no-cache-dir','--only-binary=:all:',
    '--platform','win_amd64','--python-version','312','--implementation','cp','--abi','cp312','--dest',$WheelDirectory)
& $BuildPython @pipCommon --no-deps 'torch==2.8.0+cpu' --index-url 'https://download.pytorch.org/whl/cpu'
if ($LASTEXITCODE -ne 0) { throw 'CPU torch wheel download failed.' }
$lockFile = Join-Path $taskRoot 'docs\portable-runtime-lock.txt'
if (Test-Path -LiteralPath $lockFile) {
    $downloadLock = Join-Path $taskRoot '.tools\portable-download-lock.txt'
    Get-Content -LiteralPath $lockFile | Where-Object { $_ -and $_ -notmatch '^torch==' } |
        Set-Content -LiteralPath $downloadLock -Encoding ascii
    & $BuildPython @pipCommon -r $downloadLock
} else {
    & $BuildPython @pipCommon 'numpy==1.26.2' 'matplotlib==3.8.4' 'torch-geometric==2.6.1' 'tqdm==4.67.1' 'tomli==2.2.1' `
        'filelock==3.19.1' 'typing_extensions==4.14.1' 'sympy==1.14.0' 'networkx==3.5' 'jinja2==3.1.6' 'fsspec==2025.7.0'
}
if ($LASTEXITCODE -ne 0) { throw 'Runtime wheel download failed.' }
$sitePackages = Join-Path $RuntimeDirectory 'Lib\site-packages'
$installTargets = @('torch==2.8.0+cpu','numpy==1.26.2','matplotlib==3.8.4','torch-geometric==2.6.1','tqdm==4.67.1','tomli==2.2.1')
if (Test-Path -LiteralPath $lockFile) { $installTargets = @('-r', $lockFile) }
& $BuildPython -m pip install --no-cache-dir --no-compile --no-index --find-links $WheelDirectory --target $sitePackages `
    --only-binary=:all: --platform win_amd64 --python-version 312 --implementation cp --abi cp312 @installTargets
if ($LASTEXITCODE -ne 0) { throw 'Offline runtime installation failed.' }
# Static link libraries are development assets, not DLL dependencies.
$resolvedRuntime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$resolvedTask = (Resolve-Path -LiteralPath $taskRoot).Path
if (-not $resolvedRuntime.StartsWith($resolvedTask + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Runtime pruning is restricted to this workspace.'
}
Get-ChildItem -LiteralPath (Join-Path $resolvedRuntime 'Lib\site-packages\torch\lib') -Filter '*.lib' -File |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
# Embedded Python stays isolated from Anaconda, user packages and PYTHONPATH.
[IO.File]::WriteAllText((Join-Path $RuntimeDirectory 'python312._pth'),
    "python312.zip`n.`nLib/site-packages`n../algo-service`nimport site`n", [Text.UTF8Encoding]::new($false))
$artifacts = @($embedZip) + @(Get-ChildItem -LiteralPath $WheelDirectory -Filter '*.whl' | ForEach-Object FullName)
$manifest = foreach ($artifact in $artifacts) {
    [ordered]@{ file = [IO.Path]::GetFileName($artifact); sha256 = (Get-FileHash -LiteralPath $artifact).Hash.ToLowerInvariant() }
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $RuntimeDirectory 'build-artifacts.json') -Encoding utf8NoBOM
& (Join-Path $RuntimeDirectory 'python.exe') -I -B -c 'import json,sys,torch,numpy,matplotlib,torch_geometric; print(json.dumps(dict(python=sys.version.split()[0],torch=torch.__version__,pyg=torch_geometric.__version__,cuda=torch.cuda.is_available(),isolated=sys.flags.isolated)))'
if ($LASTEXITCODE -ne 0) { throw 'Portable runtime verification failed.' }
