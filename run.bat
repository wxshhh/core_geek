@echo off
rem 《未来战争》v1.0 bot 启动脚本（Windows）：run.bat <port> [--profile <name>]
rem 用法示例：run.bat 8080
rem 优先用 py -3，其次用 python，最后回退当前目录的 .venv。
setlocal
set "ROOT=%~dp0"

if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" "%ROOT%run.py" %*
  goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%ROOT%run.py" %*
  goto :eof
)

python "%ROOT%run.py" %*
