@echo off
setlocal
title novel-dl web console
rem ============================================================
rem  novel-dl Web console launcher (ASCII only - cmd parses bat
rem  files in GBK, Chinese text here corrupts line parsing!)
rem    Double-click -> start local server + open browser
rem    CLI usage  -> novel-dl.bat <url> [-f 100 -t 200]
rem  Keep this file next to the novel_dl folder.
rem ============================================================
set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%;%PYTHONPATH%"
cd /d "%SCRIPT_DIR%"

rem ---- pick a working python (skip MS Store stub) ----
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"
if not defined PY (
    echo [novel-dl] Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

rem ---- args given: old-style CLI passthrough ----
if not "%~1"=="" (
    "%PY%" -m novel_dl %*
    set "EC=%errorlevel%"
    if not "%EC%"=="0" pause
    exit /b %EC%
)

rem ---- double-click: start web console and open browser ----
echo Starting novel-dl console... browser will open automatically.
echo Close this window to stop the service.
"%PY%" "%SCRIPT_DIR%webui.py"
pause
