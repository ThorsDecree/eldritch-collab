@echo off
setlocal
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
cd /d "%ROOT%"

where py >nul 2>nul || (
  echo VESTIGIA needs Python 3.11 or newer. Install it from https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo Preparing your local VESTIGIA doorway. This happens once.
  py -3 -m venv "%VENV%" || goto :failed
  "%VENV%\Scripts\python.exe" -m pip install --upgrade pip || goto :failed
  "%VENV%\Scripts\python.exe" -m pip install -e "%ROOT%[web-ui]" || goto :failed
)

if exist "%ROOT%.vestigia-last-home" set /p VESTIGIA_HOME=<"%ROOT%.vestigia-last-home"
if defined VESTIGIA_HOME (
  "%VENV%\Scripts\python.exe" -m vestigia web --home "%VESTIGIA_HOME%"
) else (
  "%VENV%\Scripts\python.exe" -m vestigia web
)
exit /b %errorlevel%

:failed
echo.
echo VESTIGIA could not finish setup. Check your Python installation and internet connection, then try again.
pause
exit /b 1
