@echo off
REM ============================================================
REM  WorkBuddy Daily Check-in : remove the scheduled task
REM  Double-click to run. ASCII-only on purpose.
REM  Portable: paths derived from %USERPROFILE% and %~dp0.
REM ============================================================
setlocal
set "SKILL=%~dp0"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo Removing scheduled task "WorkBuddyDailyCheckin" ...
"%PY%" "%SKILL%scripts\install_task.py" --uninstall
echo.
echo Verify (should report: task not found) ...
schtasks /Query /TN WorkBuddyDailyCheckin
echo.
pause
endlocal
