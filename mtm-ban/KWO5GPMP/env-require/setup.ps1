#Requires -Version 5.1
param(
  # Password for the postgres superuser of a new PostgreSQL 16 install. Not needed when PostgreSQL 16 is already installed.
  [string]$PostgresPassword
)
$ErrorActionPreference = "Stop"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "winget is required." }
& winget install --id Git.Git --exact --silent --accept-package-agreements --accept-source-agreements
& winget install --id OpenJS.NodeJS.LTS --exact --silent --accept-package-agreements --accept-source-agreements
& corepack enable
& corepack prepare pnpm@10.12.3 --activate

# PostgreSQL 16 on port 5433, the port used by src/.env.example.
if (Get-Service -Name "postgresql-x64-16" -ErrorAction SilentlyContinue) {
  Write-Host "PostgreSQL 16 is already installed; make sure it listens on port 5433."
} else {
  if (-not $PostgresPassword) { throw "PostgreSQL 16 is not installed. Re-run with -PostgresPassword <password for the postgres superuser>." }
  & winget install --id PostgreSQL.PostgreSQL.16 --exact --silent --accept-package-agreements --accept-source-agreements `
    --override "--mode unattended --unattendedmodeui none --superpassword $PostgresPassword --serverport 5433"
  if ($LASTEXITCODE -ne 0) { throw "PostgreSQL installation failed." }
}
Write-Host "Setup complete. Restart Windows if requested, run db-setup.sql as the postgres superuser, then run doctor.ps1." -ForegroundColor Green
