@echo off
REM ============================================================
REM  Step 5 (optional) : unregister the daily scheduled task
REM  Double-click to run. ASCII-only on purpose.
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "SKILL=%ROOT%workbuddy-desktop-checkin"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo Removing scheduled task "WorkBuddyDailyCheckin" ...
"%PY%" "%SKILL%\scripts\install_task.py" --uninstall
echo.
echo Verify (should report: cannot find the task) ...
schtasks /Query /TN WorkBuddyDailyCheckin
echo.
pause
endlocal
