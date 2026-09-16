@echo off
REM ============================================================
REM  Step 1 : prepare the isolated python environment
REM           (creates %USERPROFILE%\.workbuddy\binaries\python\envs\default
REM            and installs comtypes + pillow)
REM  Double-click to run. ASCII-only on purpose.
REM ============================================================
setlocal
set "ROOT=%~dp0"
set "SKILL=%ROOT%workbuddy-desktop-checkin"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%SKILL%\scripts\setup_env.py" (
  echo [X] Cannot find %SKILL%\scripts\setup_env.py
  echo     Keep this .cmd next to the "workbuddy-desktop-checkin" folder.
  pause
  exit /b 1
)

if not exist "%PY%" (
  echo [i] Isolated python not created yet. Looking for a bootstrap python ...
  set "PY=python"
  for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
)

echo [1/2] Bootstrapping environment with: %PY%
"%PY%" "%SKILL%\scripts\setup_env.py"
if errorlevel 1 goto fail

echo.
echo [2/2] Interpreter in use:
"%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe" "%SKILL%\scripts\setup_env.py" --print

echo.
echo DONE. Now run step 2 to register the daily task.
echo.
pause
exit /b 0

:fail
echo.
echo [X] Setup failed. Common causes:
echo     - No python at all: install Python 3.9+ from python.org first.
echo     - No network: pip cannot download comtypes / pillow.
echo     Manual retry:
echo       python "%SKILL%\scripts\setup_env.py"
echo.
pause
exit /b 1
