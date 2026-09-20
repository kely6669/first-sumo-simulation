@echo off
chcp 65001 >nul
REM ============================================================
REM  Traffic signal control demo - Python controls SUMO via TraCI
REM
REM  Just double-click this file.
REM
REM  What you will see
REM    1. A SUMO window opens and starts playing
REM    2. Vehicles arrive from all four directions
REM    3. The signal switches when PYTHON decides to, not on a
REM       fixed timer - watch for irregular green durations
REM    4. This window prints a summary when it finishes
REM
REM  Task Manager shows TWO processes while it runs:
REM      sumo-gui.exe   the simulation
REM      python.exe     the controller
REM
REM  Requirements
REM      SUMO installed and SUMO_HOME set (see README)
REM
REM  This file is intentionally ASCII-only: cmd.exe reads .bat with
REM  the system codepage, so non-ASCII characters would break it.
REM ============================================================

setlocal
set "HERE=%~dp0"
cd /d "%HERE%"

REM ---- find a usable python -------------------------------------------
REM `where python` can succeed on a Windows Store stub that does not
REM actually run scripts, so prefer known-good absolute paths first.
set "PY="
for %%P in (
    "D:\Anaconda\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
) do (
    if not defined PY if exist %%P set "PY=%%~P"
)
if not defined PY (
    for /f "delims=" %%P in ('where python 2^>nul') do (
        if not defined PY set "PY=%%P"
    )
)

if not defined PY (
    echo [ERROR] Python not found.
    echo         Install Python 3.9+ or edit this file to set PY manually.
    echo.
    pause
    exit /b 1
)

if not exist "scripts\3_signal_control.py" (
    echo [ERROR] scripts\3_signal_control.py not found.
    echo         Run this file from the repository root.
    echo.
    pause
    exit /b 1
)

echo ============================================
echo   Python + SUMO - signal control demo
echo ============================================
echo   python : %PY%
echo   SUMO   : %SUMO_HOME%
echo.
echo A SUMO window will open. The traffic light changes because
echo python tells it to, not on a fixed timer.
echo.
echo Keep this window open - the summary appears at the end.
echo.

"%PY%" "scripts\3_signal_control.py" --gui --duration 900

echo.
echo ============================================
echo   Finished. Press any key to close.
echo ============================================
pause >nul
endlocal
