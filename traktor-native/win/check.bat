@echo off
rem Compile-check ONE libtk source file (no link): check.bat <file.cpp> <tag>
rem Objects go to build\check-<tag>\ so parallel checks never share a folder.
setlocal
set HERE=%~dp0
set GUM=%HERE%vendor\frida-gum
for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -property installationPath`) do set VS=%%i
call "%VS%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
set OUT=%HERE%build\check-%2
if not exist "%OUT%" mkdir "%OUT%"
cl /nologo /std:c++17 /O2 /MT /EHsc /W3 /DNDEBUG /c /I"%GUM%" /I"%HERE%src" "%HERE%src\%1" /Fo:"%OUT%\\" && echo CHECK-OK %1
set RC=%ERRORLEVEL%
rmdir /s /q "%OUT%" 2>nul
exit /b %RC%
