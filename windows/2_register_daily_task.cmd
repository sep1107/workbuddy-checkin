@echo off
REM ============================================================
REM  Step 2 : register the daily Windows scheduled task
REM           "WorkBuddyDailyCheckin"  (default 09:00 daily)
REM  Optional: pass a time, e.g.   2_register_daily_task.cmd 08:30
REM  Double-click to run. ASCII-only on purpose.
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "SKILL=%ROOT%workbuddy-desktop-checkin"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "TM=%~1"
if "%TM%"=="" set "TM=09:00"

if not exist "%PY%" (
  echo [X] Isolated python not found. Run 1_setup_env.cmd first.
  pause
  exit /b 1
)

REM --- guard: warn if the task already exists (it may point at another copy) ---
schtasks /Query /TN WorkBuddyDailyCheckin >nul 2>nul
if not errorlevel 1 (
  echo [!] A task named "WorkBuddyDailyCheckin" ALREADY exists on this machine:
  echo.
  schtasks /Query /TN WorkBuddyDailyCheckin /V /FO LIST
  echo.
  echo     Registering now will REPOINT it to THIS folder:
  echo       %SKILL%
  echo     If the existing task already works, you probably do NOT need this
  echo     step on this machine - keep this package for another computer.
  echo.
  echo     Press Ctrl+C to abort, or
  pause
)

echo [1/2] Registering task "WorkBuddyDailyCheckin" at %TM% ...
"%PY%" "%SKILL%\scripts\install_task.py" --time %TM% --force
set "RC=%ERRORLEVEL%"

echo.
echo [2/2] Verifying ...
"%PY%" "%SKILL%\scripts\install_task.py" --verify

echo.
if "%RC%"=="0" (
  echo DONE. Check-in will run daily at %TM%.
  echo NOTE: WorkBuddy must be installed and logged in; keep the session unlocked;
  echo       the task logs into %SKILL%\logs
) else (
  echo FAILED with exit code %RC%.
  echo If schtasks.exe was blocked, open PowerShell / cmd yourself and run:
  echo   schtasks /Create /TN WorkBuddyDailyCheckin /XML "%TEMP%\WorkBuddyDailyCheckin.xml" /F
  echo Generate the XML first with:
  echo   "%PY%" "%SKILL%\scripts\install_task.py" --print-xml ^> "%TEMP%\WorkBuddyDailyCheckin.xml"
)
echo.
pause
endlocal
