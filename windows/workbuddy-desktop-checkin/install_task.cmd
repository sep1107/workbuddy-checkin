@echo off
REM ============================================================
REM  WorkBuddy Daily Check-in : register Windows scheduled task
REM  Double-click to run. Registers a daily task that calls
REM  the skill's run_checkin.py as the primary executor.
REM  Portable: paths are derived from %USERPROFILE% and %~dp0,
REM  so this works on any Windows account / any folder.
REM  ASCII-only on purpose: non-ASCII batch files get garbled
REM  by the Windows code page, so all messages stay English.
REM ============================================================
setlocal
set "SKILL=%~dp0"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  echo.
  echo [!] Isolated python not found at:
  echo     %PY%
  echo     Run step 1 first:  python scripts\setup_env.py
  echo     Trying "python" from PATH instead ...
  where python >nul 2>nul
  if errorlevel 1 (
    echo.
    echo [X] No usable python found. Install Python 3.9+ first,
    echo     then run: python "%~dp0scripts\setup_env.py"
    echo.
    pause
    exit /b 1
  )
  set "PY=python"
)

echo [1/2] Registering scheduled task "WorkBuddyDailyCheckin" ...
"%PY%" "%SKILL%scripts\install_task.py"
set "RC=%ERRORLEVEL%"

echo.
echo [2/2] Verifying ...
"%PY%" "%SKILL%scripts\install_task.py" --verify

echo.
if "%RC%"=="0" (
  echo DONE. Task registered. Check-in runs daily at 09:00.
) else (
  echo FAILED with exit code %RC%. If schtasks was blocked by a security
  echo policy, open PowerShell / cmd yourself and run:
  echo   schtasks /Create /TN WorkBuddyDailyCheckin /XML "%TEMP%\WorkBuddyDailyCheckin.xml" /F
)
echo.
echo Logs: %SKILL%logs
pause
endlocal
