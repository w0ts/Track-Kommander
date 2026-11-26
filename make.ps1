<#
.SYNOPSIS
    Track Kommander - Windows task runner (PowerShell equivalent of the Makefile).

.DESCRIPTION
    Mirrors the Makefile targets so Windows users get the same workflow without
    needing GNU Make + POSIX tools. The Makefile delegates here on Windows, but
    you can also call it directly:

        .\make.ps1 build

    Targets:
        help        list targets
        venv        uv sync - create/refresh .venv (runtime + build deps)
        lock        uv lock - refresh uv.lock
        run         run the app from source
        lint        ruff check + format src, tests, scripts
        typecheck   pyright on src/core
        test        lint, type-check src/core, then run the unit tests
        models      generate CLAP ONNX artefacts if missing
        build       Nuitka compile -> dist\TrackKommander.dist\TrackKommander.exe
        package     build + wrap into dist\TrackKommanderSetup-<ts>.msi (WiX)
        clean       remove dist\, build\
        distclean   clean + remove .venv

    'sync' is kept as a hidden alias of 'venv' for muscle memory.

    NOTE: this file is intentionally ASCII-only. Windows PowerShell 5.1 reads
    .ps1 files in the legacy ANSI codepage when there is no BOM, which corrupts
    non-ASCII glyphs and breaks parsing. Keep it plain ASCII.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---- Config (kept in sync with the Makefile) --------------------------------
$AppName       = 'Track Kommander'
$Entry         = 'src/TrackKommander.py'
$PythonVersion = '3.13'
$Venv          = '.venv'
$Dist          = 'dist'
$VenvPy        = Join-Path $Venv 'Scripts\python.exe'
$ClapOnnx      = 'clap_model\clap_audio.onnx'
$ClapEmb       = 'clap_model\clap_text_embeddings.npz'

# Run from the repo root regardless of where the caller invoked us.
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

# ---- Helpers ----------------------------------------------------------------

function Resolve-Uv {
    # Prefer a uv already on PATH (winget/scoop/pipx/standalone installer).
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) { return $uv.Source }
    throw "uv was not found on PATH. Install it, then re-run: winget install --id=astral-sh.uv -e  (or see https://docs.astral.sh/uv/getting-started/installation/)"
}

function Invoke-Native {
    # Run an external command and surface a non-zero exit code as a failure,
    # so a broken step stops the chain (make-style).
    param([Parameter(Mandatory)][string]$Exe, [string[]]$CmdArgs)
    & $Exe @CmdArgs
    if ($LASTEXITCODE -ne 0) {
        throw "command failed (exit $LASTEXITCODE): $Exe $($CmdArgs -join ' ')"
    }
}

function Ensure-Venv {
    if (-not (Test-Path $VenvPy)) {
        Write-Host "-> venv missing; creating it first" -ForegroundColor Yellow
        Invoke-Venv
    }
}

# ---- Targets ----------------------------------------------------------------

function Invoke-Help {
    Write-Host ""
    Write-Host "  Track Kommander - Windows task runner" -ForegroundColor Cyan
    Write-Host ""
    $rows = @(
        @('venv',      'uv sync - create/refresh .venv (runtime + build deps)'),
        @('lock',      'uv lock - refresh uv.lock'),
        @('run',       'run the app from source'),
        @('lockcheck', 'uv.lock matches pyproject.toml'),
        @('lint',      'ruff check + format src, tests, scripts'),
        @('typecheck', 'pyright on src/core (zero errors required)'),
        @('suppressions', 'no new noqa / type: ignore (ratchet)'),
        @('test',      'run the unit tests'),
        @('check',     'every gate, no build: lockfile, lint, types, suppressions, tests'),
        @('models',    'generate CLAP ONNX artefacts if missing'),
        @('build',     "Nuitka compile -> $Dist\TrackKommander.dist\"),
        @('package',   'build + wrap into a timestamped .msi (WiX)'),
        @('clean',     'remove build artefacts'),
        @('distclean', 'clean + remove venv')
    )
    foreach ($r in $rows) {
        Write-Host ("  {0,-12}" -f $r[0]) -ForegroundColor Green -NoNewline
        Write-Host $r[1]
    }
    Write-Host ""
    Write-Host "  usage:  make <target>   or   .\make.ps1 <target>" -ForegroundColor DarkGray
    Write-Host ""
}

function Invoke-Venv {
    $uv = Resolve-Uv
    Invoke-Native $uv @('sync', '--python', $PythonVersion, '--extra', 'build')
}

function Invoke-Lock {
    $uv = Resolve-Uv
    Invoke-Native $uv @('lock', '--python', $PythonVersion)
}

function Invoke-Run {
    Ensure-Venv
    Invoke-Native $VenvPy @($Entry)
}

function Invoke-Lint {
    # ruff is a dev-extra, so sync it in.
    $uv = Resolve-Uv
    Invoke-Native $uv @('sync', '--python', $PythonVersion,
                        '--extra', 'build', '--extra', 'dev')
    Invoke-Native $VenvPy @('-m', 'ruff', 'check', 'src', 'tests', 'scripts')
    Invoke-Native $VenvPy @('-m', 'ruff', 'format', '--check', 'src', 'tests', 'scripts')
}

function Invoke-Test {
    # The build environment has no test tools in it, so they are added here.
    $uv = Resolve-Uv
    Invoke-Native $uv @('sync', '--python', $PythonVersion,
                        '--extra', 'build', '--extra', 'dev')
    Invoke-Native $VenvPy @('-m', 'pytest', '-q', '-rs')
}

function Invoke-Lockcheck {
    $uv = Resolve-Uv
    Invoke-Native $uv @('lock', '--check')
}

function Invoke-Suppressions {
    Ensure-Venv
    Invoke-Native $VenvPy @('scripts/checks/suppressions.py')
}

function Invoke-Check {
    # Every gate, no build — the same order as `make check` on macOS.
    Invoke-Lockcheck
    Invoke-Lint
    Invoke-Typecheck
    Invoke-Suppressions
    Invoke-Test
    Write-Host 'All checks passed.'
}

function Invoke-Typecheck {
    # pyright is a dev-extra, so sync it in like Invoke-Test does.
    $uv = Resolve-Uv
    Invoke-Native $uv @('sync', '--python', $PythonVersion,
                        '--extra', 'build', '--extra', 'dev')
    Invoke-Native $VenvPy @('-m', 'pyright')
}

function Invoke-Models {
    # Export the CLAP ONNX audio encoder + text embeddings via the build-extra
    # (torch + transformers). On first run this downloads laion/clap-htsat-fused
    # (~600 MB) from HuggingFace, then writes the ~110 MB artefacts locally.
    Ensure-Venv
    if ((Test-Path $ClapOnnx) -and (Test-Path $ClapEmb)) {
        Write-Host "-> CLAP artefacts present - skipping export" -ForegroundColor DarkGray
        return
    }
    Write-Host "-> CLAP artefacts missing - exporting (one-time, downloads ~600 MB)" -ForegroundColor Yellow
    Invoke-Native $VenvPy @('scripts/export_clap_onnx.py')
}


function Invoke-Clean {
    foreach ($p in @('build', $Dist, "$Dist.lock", 'dist-nuitka')) {
        if (Test-Path $p) {
            Write-Host "  rm $p"
            Remove-Item $p -Recurse -Force
        }
    }
}

function Invoke-Distclean {
    Invoke-Clean
    foreach ($p in @($Venv, '.uv-bootstrap')) {
        if (Test-Path $p) {
            Write-Host "  rm $p"
            Remove-Item $p -Recurse -Force
        }
    }
}

# ---- Dispatch ---------------------------------------------------------------
switch ($Target.ToLower()) {
    'help'      { Invoke-Help }
    'venv'      { Invoke-Venv }
    'sync'      { Invoke-Venv }   # hidden alias
    'lock'      { Invoke-Lock }
    'run'       { Invoke-Run }
    'lockcheck' { Invoke-Lockcheck }
    'lint'      { Invoke-Lint }
    'typecheck' { Invoke-Typecheck }
    'suppressions' { Invoke-Suppressions }
    'test'      { Invoke-Test }
    'check'     { Invoke-Check }
    'models'    { Invoke-Models }
    'clean'     { Invoke-Clean }
    'distclean' { Invoke-Distclean }
    default {
        Write-Host "unknown target: $Target" -ForegroundColor Red
        Invoke-Help
        exit 1
    }
}
