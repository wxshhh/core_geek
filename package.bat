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
rem Interpreter lookup order (and why):
rem   1. python        -- the usual command; must be Python 3.10+
rem   2. .venv\Scripts\python.exe -- the interpreter vendored in this project
rem   3. py -3         -- "Python Launcher for Windows": a separate py.exe that picks
rem                       the newest installed Python 3. It ships with the official
rem                       installer and is on PATH by default, so it is the safety net
rem                       for machines where "python" is not on PATH.
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

set "PY="

rem 1) python -- the usual command, but it has to be 3.10+
where python >nul 2>nul
if errorlevel 1 goto :try_venv
python -c "import sys;sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 goto :try_venv
set "PY=python"
goto :run

:try_venv
rem 2) project-local virtualenv
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if defined PY goto :run

:try_py
rem 3) Python Launcher fallback (py.exe, ships with the official installer)
where py >nul 2>nul
if errorlevel 1 goto :nopython
set "PY=py -3"
goto :run

:nopython
echo [ERROR] no usable Python found. Install Python 3.10+ (from python.org) and tick
echo         "Add python.exe to PATH", or run this inside a project virtualenv.
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
