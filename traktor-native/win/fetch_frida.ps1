# fetch_frida.ps1 - the frida-gum devkit libtk.dll is built against, into vendor\frida-gum\
# (frida-gum.h + frida-gum.lib), downloaded from frida's GitHub release and checked against its
# sha256: the Windows side of scripts/build_libtk.sh. Nothing is done when it is already there.
#
# Bumping frida: set $Version and $Sha256 (and the version named in build.bat). The digest is on
# the release page, or: gh api repos/frida/frida/releases/tags/<version>
#   --jq '.assets[] | select(.name == "<asset>") | .digest'
#
# ASCII-only on purpose: Windows PowerShell 5.1 misreads non-ASCII in BOM-less .ps1 files.
$ErrorActionPreference = 'Stop'

$Version = '17.27.5'
$Sha256 = '4366fbc9568145d5550a7085c0b2fd0a2bcc7780d2e0e21731775ccf6276c663'
$Asset = "frida-gum-devkit-$Version-windows-x86_64.tar.xz"
$Url = "https://github.com/frida/frida/releases/download/$Version/$Asset"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Gum = Join-Path $Here 'vendor\frida-gum'
$Stamp = Join-Path $Gum '.version'

if ((Test-Path (Join-Path $Gum 'frida-gum.lib')) -and (Test-Path $Stamp) -and
    ((Get-Content $Stamp -Raw).Trim() -eq $Version)) {
    exit 0
}

$Tarball = Join-Path $env:TEMP "$Asset.$PID"
try {
    Write-Host "> downloading $Url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Tarball
    $got = (Get-FileHash -Algorithm SHA256 $Tarball).Hash.ToLower()
    if ($got -ne $Sha256) { throw "sha256 mismatch for ${Asset}: got $got, expected $Sha256" }
    if (Test-Path $Gum) { Remove-Item -Recurse -Force $Gum }
    New-Item -ItemType Directory -Force $Gum | Out-Null
    # Windows' own tar (bsdtar) reads .tar.xz.
    & "$env:SystemRoot\System32\tar.exe" -xf $Tarball -C $Gum
    if ($LASTEXITCODE -ne 0) { throw "extracting $Asset failed (tar exit $LASTEXITCODE)" }
    if (-not (Test-Path (Join-Path $Gum 'frida-gum.lib'))) { throw "no frida-gum.lib in $Asset" }
    Set-Content -Encoding ascii $Stamp $Version
    Write-Host "frida-gum $Version in $Gum"
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $Tarball
}
