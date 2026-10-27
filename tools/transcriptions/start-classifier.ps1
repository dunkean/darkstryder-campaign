param(
    [Parameter(Mandatory = $true)][string]$ModelPath,
    [string]$PythonExecutable,
    [string]$ServedModelName = 'Qwen/Qwen3.8-27B',
    [int]$Port = 8000,
    [int]$ContextTokens = 32768,
    [string]$Dtype = 'bfloat16'
)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
if (-not $PythonExecutable) {
    $PythonExecutable = Join-Path $projectRoot 'runtime/phase4-classifier/.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw 'Classifier environment missing. Follow content/transcriptions/PHASE4.md (separate environment).'
}
$resolvedModelPath = (Resolve-Path -LiteralPath $ModelPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedModelPath 'config.json') -PathType Leaf)) {
    throw 'ModelPath must be an existing local Hugging Face model directory (not GGUF).'
}
# Invoked manually only. No downloads, installation, background jobs or automatic GPU takeover.
$previousHubOffline = $env:HF_HUB_OFFLINE
$previousTransformersOffline = $env:TRANSFORMERS_OFFLINE
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
try {
    & $PythonExecutable -m hf_server --model $resolvedModelPath --served-model-name $ServedModelName `
        --host 127.0.0.1 --port $Port --device auto --dtype $Dtype `
        --max-model-len $ContextTokens --max-batch-size 1 --max-batch-tokens $ContextTokens `
        --classifier-prompt-policy examples_binary --enforce-model-id
    if ($LASTEXITCODE -ne 0) { throw "Classifier exited with code $LASTEXITCODE" }
} finally {
    $env:HF_HUB_OFFLINE = $previousHubOffline
    $env:TRANSFORMERS_OFFLINE = $previousTransformersOffline
}
