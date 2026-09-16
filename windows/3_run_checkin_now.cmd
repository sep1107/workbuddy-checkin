@echo off
REM ============================================================
REM  Step 3 : run one check-in right now (manual test)
REM  Runs the unattended entry point run_checkin.py --json so the
REM  result is also written to the skill's logs folder.
REM  Optional: pass --dry to only read the state, without claiming.
REM  Double-click to run. ASCII-only on purpose.
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "SKILL=%ROOT%workbuddy-desktop-checkin"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  echo [X] Isolated python not found. Run 1_setup_env.cmd first.
  pause
  exit /b 1
)

echo Running check-in now. The mouse will be used for a few seconds,
echo please do not touch it until this window prints a result.
echo.
"%PY%" "%SKILL%\scripts\run_checkin.py" --json %*
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
echo Exit code %RC% :
echo   0 = claimed now, or already claimed today  [OK]
echo   2 = needs manual attention (not logged in / UI changed)
echo   3 = execution error
echo   4 = skipped (screen locked, or WorkBuddy not running)
echo.
echo Result file: %SKILL%\logs\result-YYYY-MM-DD.json  (today's date)
echo Log file   : %SKILL%\logs\checkin-YYYY-MM.log
echo ------------------------------------------------------------
echo.
pause
endlocal
