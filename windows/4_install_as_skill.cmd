@echo off
REM ============================================================
REM  Step 4 (optional) : install this skill into WorkBuddy
REM  Copies the folder to %USERPROFILE%\.workbuddy\skills\ so
REM  WorkBuddy can load it by name (workbuddy-desktop-checkin)
REM  and so the fallback automation can find it.
REM  Double-click to run. ASCII-only on purpose.
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "SRC=%ROOT%workbuddy-desktop-checkin"
set "DST=%USERPROFILE%\.workbuddy\skills\workbuddy-desktop-checkin"

if not exist "%SRC%\SKILL.md" (
  echo [X] Cannot find %SRC%\SKILL.md
  echo     Keep this .cmd next to the "workbuddy-desktop-checkin" folder.
  pause
  exit /b 1
)

echo Installing skill to:
echo   %DST%
if exist "%DST%" echo   (existing files will be refreshed, logs are kept)
echo.

robocopy "%SRC%" "%DST%" /E /XD logs __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /NP
set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo [X] robocopy failed with code %RC%
  pause
  exit /b 1
)

echo DONE. Skill installed.
echo.
echo Next steps on this machine:
echo   1. Register the task:  %DST%\install_task.cmd
echo   2. In WorkBuddy, create a fallback automation (daily, e.g. 10:30)
echo      whose prompt tells it to read
echo        %DST%\logs\result-YYYY-MM-DD.json
echo      and only run scripts\run_checkin.py --json if today's status is not
echo      success / already_claimed. See README.md for the ready-made prompt.
echo.
pause
endlocal
