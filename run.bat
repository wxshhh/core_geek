@echo off
rem Future War v1.0 bot launcher (Windows): run.bat <port> [--profile <name>]
rem Example: run.bat 18080
rem
rem Interpreter lookup order (and why):
rem   1. python        -- the usual command; must be Python 3.10+
rem   2. .venv\Scripts\python.exe -- the interpreter vendored in this project
rem   3. py -3         -- "Python Launcher for Windows": a SEPARATE py.exe shipped
rem                       with the official installer that picks the newest installed
rem                       Python 3. It is not an alias of "python"; it is the safety
rem                       net for machines where "python" is not on PATH.
rem
rem No pause here on purpose: the judge platform launches this and waits on the
rem process; pausing would hang the match.
rem
rem NOTE: this file is intentionally ASCII-only. cmd.exe reads .bat files using the
rem console/OEM code page, so non-ASCII text here can be garbled or break parsing.
rem Chinese docs: README.md, config\README.md.
setlocal
set "PYTHONIOENCODING=utf-8"
set "ROOT=%~dp0"
rem Anchor the working directory to the project root, so logs\ lands next to the
rem project (run.sh does the same with cd "$ROOT_DIR").
cd /d "%ROOT%"

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
exit /b 1

:run
%PY% "%ROOT%run.py" %*
exit /b %ERRORLEVEL%
