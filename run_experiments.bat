@echo off
chcp 65001 >nul
REM ============================================================
REM  Run the full comparison experiment, then aggregate results
REM
REM  Just double-click this file.
REM
REM  It runs twice as many simulations as the defaults, then calls
REM  the pandas aggregation so you get the comparison table on
REM  screen without typing anything else.
REM
REM  What happens
REM     1. generate_network.py   rebuild the network (fast)
REM     2. run_experiments.py    2 strategies x 3 headway scales
REM                              x 4 seeds = 24 simulations
REM     3. analyse_results.py    aggregate and report mean +/- std
REM
REM  Results land in results\ :  index.csv and summary.csv
REM  Raw per-run files land in results\runs\ (not tracked by git)
REM
REM  This takes a few minutes. The window prints progress as it goes.
REM
REM  ASCII-only on purpose (cmd.exe reads .bat with the system codepage).
REM ============================================================

setlocal
cd /d "%~dp0"

REM ---- find python -----------------------------------------------------
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
    echo [ERROR] Python not found. Install Python 3.9+ first.
    pause
    exit /b 1
)

echo ============================================
echo   Experiment: signal strategies compared
echo ============================================
echo   python : %PY%
echo.
echo Step 1/3  rebuild network
"%PY%" "scripts\generate_network.py" || goto :failed

echo.
echo Step 2/3  run simulations (this is the slow part)
"%PY%" "scripts\run_experiments.py" --runs 4 --duration 900 --demands 0.8 1.0 1.3 || goto :failed

echo.
echo Step 3/3  aggregate with pandas
"%PY%" "scripts\analyse_results.py" --save || goto :failed

echo.
echo ============================================
echo   Done. Open results\summary.csv to see the table.
echo ============================================
pause >nul
exit /b 0

:failed
echo.
echo [ERROR] a step failed - see the messages above.
echo.
pause
exit /b 1
