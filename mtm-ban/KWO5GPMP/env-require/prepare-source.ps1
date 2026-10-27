#Requires -Version 5.1
$ErrorActionPreference = "Stop"
$source = Resolve-Path (Join-Path $PSScriptRoot "..\src")
Push-Location $source
try {
  & pnpm install --frozen-lockfile
  if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
  & pnpm run build
  if ($LASTEXITCODE -ne 0) { throw "Build verification failed." }
} finally { Pop-Location }
Write-Host "Source environment is ready." -ForegroundColor Green
