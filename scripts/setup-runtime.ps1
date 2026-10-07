[CmdletBinding()]
param([string] $BasePython = 'D:\Anaconda3\envs\pytorch_cuda\python.exe')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root '.runtime'
$env:TEMP = Join-Path $root '.tools\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'Scripts\python.exe'))) {
    & $BasePython -m venv --system-site-packages $runtime
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create independent runtime.' }
}
& (Join-Path $runtime 'Scripts\python.exe') -m pip install --no-cache-dir --no-deps --disable-pip-version-check tomli==2.2.1 torch-geometric==2.6.1
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed.' }
& (Join-Path $runtime 'Scripts\python.exe') -c 'import json,torch,tomli,torch_geometric,numpy; print(json.dumps(dict(torch=torch.__version__,pyg=torch_geometric.__version__,numpy=numpy.__version__,cuda=torch.cuda.is_available())))'
