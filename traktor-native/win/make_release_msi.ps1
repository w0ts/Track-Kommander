# make_release_msi.ps1 — the Track Kommander installer for Windows (TrackKommanderSetup.msi):
# builds libtk.dll + tk_launch.exe (build.bat), the kit (make_kit.ps1), stages them in one folder
# and wraps it with WiX v7 (installer\track-kommander.wxs). An edition with an add-on passes its
# tk-addon folder with -Addon: it is staged beside the kit (one installer, User 2026-11-26).
#
# Usage: powershell -ExecutionPolicy Bypass -File make_release_msi.ps1 -Out <msi> [-Addon <dir>]
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$Addon = ''
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path (Join-Path $Here '..\..')).Path

function Invoke-Checked([string]$What, [scriptblock]$Run) {
    & $Run
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

if ($Addon -and -not (Test-Path (Join-Path $Addon 'manifest.json'))) { throw "not an add-on (no manifest.json): $Addon" }

Write-Host '> libtk.dll + tk_launch.exe (build.bat)'
Invoke-Checked 'build.bat' { cmd /c (Join-Path $Here 'build.bat') }

$Stage = Join-Path $Repo 'worktrees\release-msi\stage'
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force $Stage | Out-Null
Copy-Item (Join-Path $Here 'build\tk_launch.exe'), (Join-Path $Here 'build\libtk.dll') $Stage

Write-Host '> kit'
Invoke-Checked 'make_kit' { powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Here 'make_kit.ps1') $Stage -Name tk }

if ($Addon) {
    Write-Host "> add-on: $Addon"
    robocopy $Addon (Join-Path $Stage 'tk-addon') /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying the add-on failed (robocopy $LASTEXITCODE)" }
}

# MSI version: 1.0.<days since 2020-01-01>, above every earlier build (see the .wxs).
$days = [int]((Get-Date).Date - [datetime]'2020-01-01').TotalDays
$Version = "1.0.$days"

if (-not (Get-Command wix -ErrorAction SilentlyContinue)) { throw 'WiX not found: dotnet tool install --global wix' }
Write-Host "> wix build ($Version) -> $Out"
$OutFull = [IO.Path]::GetFullPath($Out)
New-Item -ItemType Directory -Force (Split-Path -Parent $OutFull) | Out-Null
Invoke-Checked 'wix build' {
    wix build (Join-Path $Here 'installer\track-kommander.wxs') -acceptEula wix7 -arch x64 `
        -ext WixToolset.UI.wixext -ext WixToolset.Util.wixext `
        -d "Version=$Version" -d "Stage=$Stage" `
        -d "LicenseRtf=$(Join-Path $Repo 'scripts\wix\license.rtf')" `
        -d "Icon=$(Join-Path $Repo 'assets\icons\tk.ico')" `
        -o $OutFull
}
$hash = (Get-FileHash -Algorithm SHA256 $OutFull).Hash.ToLower()
Set-Content -Encoding ascii "$OutFull.sha256" "$hash  $(Split-Path -Leaf $OutFull)"
Remove-Item -Recurse -Force $Stage
Write-Host "built: $OutFull ($([math]::Round((Get-Item $OutFull).Length / 1MB)) MB)"
