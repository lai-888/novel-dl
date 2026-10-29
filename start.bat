@echo off
rem ASCII only. cmd parses bat in the system code page (GBK on this machine),
rem so Chinese characters here would corrupt line parsing.
setlocal
cd /d "%~dp0"

if not "%~1"=="" goto cli

rem ---- double click: start the local web console ----
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"
if not defined PY (
    echo Python not found. Install Python 3.10+ first: https://www.python.org/downloads/
    pause
    exit /b 1
)

rem --lan lets your phone join on the same Wi-Fi. Remove it if you do not need that.
"%PY%" "%~dp0webui.py" --lan
if errorlevel 1 pause
exit /b 0

:cli
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"
if not defined PY (
    echo Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)
"%PY%" -m novel_dl %*
if errorlevel 1 pause
