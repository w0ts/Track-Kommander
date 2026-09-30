@echo off
rem Windows entry point for build_ffmpeg_lgpl.sh: load the MSVC x64
rem environment, then run the shared build script under MSYS2 bash with the
rem Windows PATH (and so cl.exe, uv, python) inherited.
rem
rem Needs: Visual Studio 2022 (Build Tools is enough) with the C++ x64 tools,
rem and MSYS2 with:  pacman -S --needed make diffutils nasm pkgconf
setlocal

if not defined MSYS2_ROOT set "MSYS2_ROOT=C:\msys64"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
    echo No Visual Studio with the C++ x64 tools found. 1>&2
    exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1

set MSYS2_PATH_TYPE=inherit
set CHERE_INVOKING=1
"%MSYS2_ROOT%\usr\bin\bash.exe" -l "%~dp0build_ffmpeg_lgpl.sh" %*
exit /b %ERRORLEVEL%
