@echo off
rem Future War v1.0 packaging script (Windows) -- double-click this file to build the archive.
rem
rem Output: dist\future-war-bot-<version>.tar.gz  (+ .sha256)
rem         The archive contains ONE top-level directory named CoreGeek\
rem         (e.g. CoreGeek\main3.py, CoreGeek\src\...), so the platform sees a project dir.
rem
rem Usage from cmd:
rem   package.bat                      version from the VERSION file
rem   package.bat 0.2.0                override the version
rem   package.bat --flat               no top-level dir (main3.py at archive root)
rem   package.bat --prefix MyDir       rename the top-level dir
rem
rem NOTE: this file is intentionally ASCII-only. cmd.exe reads .bat files using the
rem console/OEM code page, so non-ASCII text here can be garbled or break parsing.
rem Chinese docs: README.md, config\README.md.
setlocal
chcp 65001 >nul 2>nul
set "PYTHONIOENCODING=utf-8"
set "ROOT=%~dp0"
cd /d "%ROOT%"

rem When double-clicked (cmdcmdline contains this script name), pause at the end so
rem the console window does not vanish before the result can be read.
set "PAUSE_AT_END="
echo "%cmdcmdline%" | find /i "%~nx0" >nul 2>nul && set "PAUSE_AT_END=1"

if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PY=%ROOT%.venv\Scripts\python.exe"
  goto :run
)

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
  goto :run
)

where python >nul 2>nul
if %errorlevel%==0 (
  set "PY=python"
  goto :run
)

echo [ERROR] Python not found. Install Python 3.10+ and tick "Add python.exe to PATH":
echo         https://www.python.org/downloads/windows/
if defined PAUSE_AT_END pause
exit /b 1

:run
echo [INFO] using Python: %PY%
%PY% "%ROOT%scripts\package.py" %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" (
  echo [ERROR] packaging failed, exit code %RC%
) else (
  echo [DONE] archive written to dist\  ^(extracts to a single CoreGeek\ directory^)
)
if defined PAUSE_AT_END pause
exit /b %RC%
