#!/usr/bin/env pwsh
$ErrorActionPreference = "Stop"
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".cache/uv"

uv run --all-packages --locked --python 3.12.10 python "$PSScriptRoot/scripts/dev.py" @args
exit $LASTEXITCODE
