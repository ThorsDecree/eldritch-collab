@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "RUNTIME_PYTHON=%VENV%\Scripts\python.exe"
cd /d "%ROOT%" || goto :failed

if not exist "%RUNTIME_PYTHON%" (
  call :create_venv || goto :failed
)

rem A suite checkout needs both the client SDK and the local stdio server.
set "MCP_SOURCE=..\VESTIGIA_MCP_Server"
set "VESTIGIA_RUNTIME_INSTALL_ATTEMPTED="
:check_environment
"%RUNTIME_PYTHON%" -I -c "from pathlib import Path; from importlib.util import find_spec; import vestigia, discord, fastapi, uvicorn; assert Path(vestigia.__file__).resolve().is_relative_to(Path.cwd() / 'src'); assert find_spec('python_multipart') or find_spec('multipart')" >nul 2>nul
if errorlevel 1 goto :install
if exist "%MCP_SOURCE%\pyproject.toml" (
  "%RUNTIME_PYTHON%" -I -c "from pathlib import Path; from mcp import Client, StdioServerParameters; import vestigia_mcp.cli; assert Path(vestigia_mcp.cli.__file__).resolve().is_relative_to(Path.cwd().parent / 'VESTIGIA_MCP_Server' / 'src')" >nul 2>nul
  if errorlevel 1 goto :install
)
goto :launch

:install
if defined VESTIGIA_RUNTIME_INSTALL_ATTEMPTED goto :failed
set "VESTIGIA_RUNTIME_INSTALL_ATTEMPTED=1"
echo Preparing the Runtime environment.
if exist "%MCP_SOURCE%\pyproject.toml" (
  "%RUNTIME_PYTHON%" -I -m pip install -e ".[web-ui,discord,mcp-context]" -e "%MCP_SOURCE%" || goto :failed
) else (
  "%RUNTIME_PYTHON%" -I -m pip install -e ".[web-ui,discord]" || goto :failed
)
goto :check_environment

:launch
if /i "%~1"=="--prepare" exit /b 0
if not "%~1"=="" goto :explicit_command
if not defined VESTIGIA_HOME if exist "%ROOT%.vestigia-last-home" set /p VESTIGIA_HOME=<"%ROOT%.vestigia-last-home"
if defined VESTIGIA_HOME (
  "%RUNTIME_PYTHON%" -I -m vestigia web --home "%VESTIGIA_HOME%"
) else (
  "%RUNTIME_PYTHON%" -I -m vestigia web
)
exit /b %errorlevel%

:explicit_command
"%RUNTIME_PYTHON%" -I -m vestigia %*
exit /b %errorlevel%

:create_venv
where py >nul 2>nul || (
  echo VESTIGIA needs Python 3.11 or newer. Install it from https://www.python.org/downloads/
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul || (
  echo VESTIGIA needs Python 3.11 or newer. Install it from https://www.python.org/downloads/
  exit /b 1
)
py -3 -m venv "%VENV%"
exit /b %errorlevel%

:failed
echo.
echo VESTIGIA could not finish setup. Check your Python installation and internet connection, then try again.
if /i not "%~1"=="--prepare" pause
exit /b 1
