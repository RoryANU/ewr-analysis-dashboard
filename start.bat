@echo off
rem Start the EWR analysis tool (Windows). Double-click this file.
cd /d "%~dp0"

set "PYCMD="
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
  python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>&1 && set "PYCMD=python"
)

if not defined PYCMD (
  echo.
  echo Python 3.9 or newer was not found on this computer.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and then start this again.
  echo.
  pause
  exit /b 1
)

%PYCMD% run.py %*
set "STATUS=%errorlevel%"
if not "%STATUS%"=="0" (
  echo.
  echo The tool stopped with a problem ^(see above^).
  pause
)
exit /b %STATUS%
