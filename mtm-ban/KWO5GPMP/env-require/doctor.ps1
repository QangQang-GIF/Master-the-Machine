#Requires -Version 5.1
param(
  [string]$Output = (Join-Path $PSScriptRoot "environment-report.json"),
  # Application folder whose node_modules provides the Prisma CLI and whose .env may hold TEST_DATABASE_URL.
  [string]$SourceDir = (Join-Path $PSScriptRoot "..\src"),
  [switch]$SkipDatabase
)
$ErrorActionPreference = "Stop"
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
function Test-Command([string]$Name) {
  $command = Get-Command $Name -ErrorAction SilentlyContinue
  [pscustomobject]@{ name = $Name; pass = [bool]$command; location = $(if ($command) { $command.Source } else { $null }); detail = $null }
}

# The existing tests clean the database with `SET session_replication_role = replica`
# (tests/helpers.ts), which needs a superuser or, on PostgreSQL 15+,
# `GRANT SET ON PARAMETER session_replication_role TO <role>`, and create the test
# database when it is missing (tests/global-setup.ts), which needs CREATEDB.
# db-setup.sql grants both.
function Get-TestDatabaseUrl([string]$Dir) {
  if ($env:TEST_DATABASE_URL) { return $env:TEST_DATABASE_URL }
  $envFile = Join-Path $Dir ".env"
  if (-not (Test-Path -LiteralPath $envFile)) { return $null }
  foreach ($line in Get-Content -LiteralPath $envFile) {
    if ($line -match '^\s*TEST_DATABASE_URL\s*=\s*(.+?)\s*$') { return $Matches[1].Trim('"', "'") }
  }
  return $null
}

function Test-DatabasePrivileges([string]$Dir) {
  $check = [pscustomobject]@{ name = "test-database-privileges"; pass = $false; location = $null; detail = $null }
  $url = Get-TestDatabaseUrl $Dir
  if (-not $url) { $check.pass = $null; $check.detail = "skipped: TEST_DATABASE_URL is not set (environment or src/.env)"; return $check }
  if (-not (Test-Path -LiteralPath (Join-Path $Dir "node_modules\.bin"))) { $check.pass = $null; $check.detail = "skipped: run pnpm install in src first"; return $check }
  $check.location = ($url -replace '//([^:/@]+):[^@]*@', '//$1:***@')
  # Connect to the maintenance database, as tests/global-setup.ts does, so the check also
  # works before the test database exists.
  $testDb = ([uri]$url).AbsolutePath.TrimStart('/')
  $adminUrl = $url -replace '(//[^/]+/)[^?]*', '${1}postgres'
  $sql = @"
SET session_replication_role = replica; SET session_replication_role = DEFAULT;
DO `$`$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND (rolcreatedb OR rolsuper))
     AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$($testDb -replace "'", "''")') THEN
    RAISE EXCEPTION 'missing CREATEDB';
  END IF;
END `$`$;
"@
  Push-Location -LiteralPath $Dir
  try {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $result = $sql | & pnpm exec prisma db execute --stdin --url $adminUrl 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
  } finally { Pop-Location }
  if ($code -eq 0) { $check.pass = $true; $check.detail = "TEST_DATABASE_URL role can set session_replication_role and create the test database" }
  elseif ($result -match "permission denied to set parameter") {
    $check.detail = "TEST_DATABASE_URL role cannot set session_replication_role; pnpm test will fail. Run env-require\db-setup.sql as the postgres superuser (PostgreSQL 15+)."
  } elseif ($result -match "missing CREATEDB") {
    $check.detail = "TEST_DATABASE_URL role cannot create the test database $testDb; pnpm test will fail. Run env-require\db-setup.sql as the postgres superuser."
  } else {
    $check.detail = "could not run the check: " + (($result -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join " ").Trim()
  }
  return $check
}

$checks = @()
$checks += Test-Command "git"
$checks += Test-Command "node"
$checks += Test-Command "pnpm"
if (-not $SkipDatabase) { $checks += Test-DatabasePrivileges $SourceDir }
# Every check must pass. The database check is skipped only with -SkipDatabase.
$status = if (@($checks | Where-Object { $_.pass -ne $true }).Count -eq 0) { "READY" } else { "NOT_READY" }
$report = [ordered]@{ schemaVersion = 1; generatedAt = (Get-Date).ToUniversalTime().ToString("o"); status = $status; checks = $checks }
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Output -Encoding UTF8
$checks | Format-Table name, pass, location, detail -AutoSize -Wrap
Write-Host "Environment status: $status"
if ($status -ne "READY") { exit 2 }
