# make_kit.ps1 — the Track Kommander KIT for Windows: what TK inside Traktor needs on a PC with no
# TK checkout — Python 3.13, TK's packages and TK's source, in the repo's own layout, because
# libtk.dll and TK look for exactly these paths under TK_REPO (the launcher sets it to <install>\tk):
#
#   <kit>\python\                          the interpreter (TK_PYTHONHOME; python313.dll)
#   <kit>\.venv\Lib\site-packages          TK's locked runtime packages (no dev, no build)
#   <kit>\src\  traktor-native\win\embed_tk_win.py  assets\ Traktor\ licenses\ clap_model\ ...
#   <kit>\KIT-VERSION                      "<TK version> <commit>[+local]"
#
# No venv interpreter: libtk.dll puts site-packages on sys.path itself, and worker processes run
# <kit>\python\pythonw.exe with the parent's sys.path ("spawn"). Then trimmed (..\re\trim_kit.py)
# and smoke-tested (..\re\smoke_kit.py), as on the Mac (re/make_kit.sh).
#
# Usage: powershell -ExecutionPolicy Bypass -File make_kit.ps1 <out-dir> [-Name tk]
param(
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$Name = 'tk'
)
$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = if ($env:TK_REPO_OVERRIDE) { $env:TK_REPO_OVERRIDE } else { (Resolve-Path (Join-Path $Here '..\..')).Path }

function Invoke-Checked([string]$What, [scriptblock]$Run) {
    & $Run
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

New-Item -ItemType Directory -Force $OutDir | Out-Null
$Kit = Join-Path (Resolve-Path $OutDir).Path $Name
if (Test-Path $Kit) {
    # Only ever our own earlier build (it carries the marker).
    if (-not (Test-Path (Join-Path $Kit 'KIT-VERSION'))) { throw "$Kit exists and is not a kit: not touched" }
    Remove-Item -Recurse -Force $Kit
}
New-Item -ItemType Directory $Kit | Out-Null

# 1. The interpreter TK's .venv was made from.
$base = (& "$Repo\.venv\Scripts\python.exe" -c 'import sys; print(sys.base_prefix)').Trim()
Write-Host "[1/5] python: $base"
Copy-Item -Recurse $base (Join-Path $Kit 'python')
$sp = Join-Path $Kit 'python\Lib\site-packages'
if (Test-Path $sp) { Get-ChildItem $sp | Remove-Item -Recurse -Force }

# 2. TK's runtime packages, from uv.lock.
Write-Host "[2/5] TK's runtime packages (uv.lock, no dev, no build extra)"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Kit '.venv'
Push-Location $Repo
try {
    Invoke-Checked 'uv sync' { uv sync --frozen --no-dev --no-install-project --python (Join-Path $Kit 'python\python.exe') --quiet }
} finally {
    Pop-Location
    Remove-Item Env:UV_PROJECT_ENVIRONMENT
}
# Only the packages are used: the venv's launchers and pyvenv.cfg name this PC's paths.
foreach ($f in 'Scripts', 'pyvenv.cfg', '.lock', 'CACHEDIR.TAG', '.gitignore') {
    $p = Join-Path $Kit ".venv\$f"
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

# 3. TK's source and data.
Write-Host "[3/5] TK's source and data"
foreach ($d in 'src', 'assets', 'Traktor', 'licenses') {
    robocopy (Join-Path $Repo $d) (Join-Path $Kit $d) /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying $d failed (robocopy $LASTEXITCODE)" }
}
foreach ($f in 'pyproject.toml', 'LICENSE', 'THIRD_PARTY.md') { Copy-Item (Join-Path $Repo $f) $Kit }
New-Item -ItemType Directory -Force (Join-Path $Kit 'traktor-native\win'), (Join-Path $Kit 'clap_model') | Out-Null
Copy-Item (Join-Path $Repo 'traktor-native\win\embed_tk_win.py') (Join-Path $Kit 'traktor-native\win')
foreach ($f in 'clap_audio.onnx', 'clap_text_embeddings.npz') {
    $src = Join-Path $Repo "clap_model\$f"
    if (-not (Test-Path $src)) { throw "no $src (make models, or copy it from a Mac)" }
    Copy-Item $src (Join-Path $Kit 'clap_model')
}

# 4. Trim to what TK loads.
Write-Host "[4/5] trim to what TK loads (trim_kit.py)"
Set-Content -Encoding ascii (Join-Path $Kit 'KIT-VERSION') 'trimming'   # trim_kit.py only touches a kit
$py = Join-Path $Kit 'python\python.exe'
Invoke-Checked 'trim' { & $py -B (Join-Path $Repo 'traktor-native\re\trim_kit.py') $Kit }

# 5. Marker, then the smoke test the way libtk.dll starts it. Caches go to a scratch folder.
Write-Host "[5/5] marker + smoke test"
$version = (Select-String -Path (Join-Path $Repo 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (Test-Path (Join-Path $Repo '.git')) {
    $commit = (git -C $Repo rev-parse --short HEAD).Trim()
    if (git -C $Repo status --porcelain -- src traktor-native assets Traktor pyproject.toml uv.lock) { $commit = "$commit+local" }
} elseif ($env:TK_SOURCE_COMMIT) {
    $commit = $env:TK_SOURCE_COMMIT  # a snapshot (git archive) of that commit
} else {
    throw "not a git checkout: set TK_SOURCE_COMMIT to the commit this source is"
}
Set-Content -Encoding ascii (Join-Path $Kit 'KIT-VERSION') "$version $commit"
$cache = Join-Path $env:TEMP ("tk-kit-smoke-" + [guid]::NewGuid())
New-Item -ItemType Directory $cache | Out-Null
$env:PYTHONHOME = Join-Path $Kit 'python'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:NUMBA_CACHE_DIR = $cache
try {
    Invoke-Checked 'smoke test' { & $py (Join-Path $Repo 'traktor-native\re\smoke_kit.py') $Kit }
} finally {
    Remove-Item Env:PYTHONHOME, Env:PYTHONDONTWRITEBYTECODE, Env:NUMBA_CACHE_DIR
    Remove-Item -Recurse -Force $cache
}
$mb = [math]::Round(((Get-ChildItem -Recurse -File $Kit | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "built: $Kit ($version $commit, $mb MB)"
