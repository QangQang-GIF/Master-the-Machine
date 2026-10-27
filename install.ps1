param(
    [Parameter(Mandatory=$true)][string]$TeamWorkspace
)
$ErrorActionPreference = 'Stop'
$ScriptDir = $PSScriptRoot
py -3.12 (Join-Path $ScriptDir "scripts\install.py") --team-workspace $TeamWorkspace --source-dir $ScriptDir
if ($LASTEXITCODE -ne 0) {
    throw "TEAM_PACK_INSTALL_FAILED"
}
