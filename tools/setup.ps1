param(
    [string]$SourceRoot = 'D:/Drive_google/DarkStryder',
    [string]$RuntimeRoot = 'D:/Workspace/Self/RPG/darkstryder_runtime'
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath $SourceRoot)) { throw 'Source folder missing' }
@{ sourceRoot=$SourceRoot; runtimeRoot=$RuntimeRoot } | ConvertTo-Json | Set-Content -LiteralPath 'config.local.json' -Encoding utf8
python tools/inventory/bootstrap.py
if ($LASTEXITCODE -ne 0) { throw 'Bootstrap failed' }
npm.cmd ci
