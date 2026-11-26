@echo off
rem Build libtk.dll + tk_launch.exe (Windows x64, MSVC, static CRT).
rem Needs the frida-gum devkit (windows-x86_64, 17.27.5) in vendor\frida-gum\ (frida-gum.h + .lib):
rem fetch_frida.ps1 downloads it (sha256-checked) when it is missing.
rem Output: build\libtk.dll, build\tk_launch.exe
setlocal
set HERE=%~dp0
set GUM=%HERE%vendor\frida-gum
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%fetch_frida.ps1" || exit /b 1
if not exist "%GUM%\frida-gum.lib" (
  echo need %GUM%\frida-gum.lib ^(frida-gum-devkit-17.27.5-windows-x86_64^)
  exit /b 1
)
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationPath`) do set VS=%%i
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
if not exist "%HERE%build" mkdir "%HERE%build"
if not exist "%HERE%build\obj" mkdir "%HERE%build\obj"
pushd "%HERE%build\obj"
rem every tkw_*.cpp is a part of libtk.dll (tk_launch.cpp is the launcher)
rem /Zi + /DEBUG /MAP: libtk.pdb and libtk.map next to the dll, so a "CRASH: ... libtk+0x..." line
rem resolves to a function (OPT:REF,ICF keep the release layout that /DEBUG alone would change)
cl /nologo /std:c++17 /O2 /MT /EHsc /W3 /LD /Zi /DNDEBUG /I"%GUM%" /I"%HERE%src" ^
   "%HERE%src\tkw_*.cpp" ^
   /Fe:"%HERE%build\libtk.dll" /link /DEBUG /OPT:REF /OPT:ICF /MAP:"%HERE%build\libtk.map" ^
   /LIBPATH:"%GUM%" user32.lib advapi32.lib ole32.lib shell32.lib crypt32.lib || (popd & exit /b 1)
cl /nologo /std:c++17 /O2 /MT /EHsc /W3 /DUNICODE /D_UNICODE "%HERE%src\tk_launch.cpp" ^
   /Fe:"%HERE%build\tk_launch.exe" /link user32.lib /SUBSYSTEM:WINDOWS || (popd & exit /b 1)
popd
echo built: %HERE%build\libtk.dll %HERE%build\tk_launch.exe
