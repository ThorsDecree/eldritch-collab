@echo off
setlocal
title VESTIGIA MCP Tunnel

set "ROOT=%~dp0"
set "REPO_ROOT=%ROOT%.."
set "TUNNEL_EXE=%ROOT%tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe"
set "PROFILE=vestigia-local"
if not "%~1"=="" set "PROFILE=%~1"

for %%I in ("%ROOT%..\VESTIGIA") do set "VESTIGIA_MCP_LIVE_ARCHIVE_ROOT=%%~fI"
for %%I in ("%ROOT%..\VESTIGIA\Anima.zip") do set "VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT=%%~fI"
if not defined VESTIGIA_MCP_STATE_DIR set "VESTIGIA_MCP_STATE_DIR=%USERPROFILE%\.vestigia-mcp"
if not defined VESTIGIA_MCP_DEPLOYMENT_ID set "VESTIGIA_MCP_DEPLOYMENT_ID=jeff-desktop"

rem Source revision evidence is gathered at the operator-controlled launcher boundary.
rem system.identity itself never shells out to git. Missing git/repo evidence stays unknown.
set "VESTIGIA_MCP_SOURCE_COMMIT="
set "VESTIGIA_MCP_SOURCE_STATE=unknown"
where git >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%G in ('git -C "%REPO_ROOT%" rev-parse HEAD 2^>nul') do set "VESTIGIA_MCP_SOURCE_COMMIT=%%G"
    if defined VESTIGIA_MCP_SOURCE_COMMIT set "VESTIGIA_MCP_SOURCE_STATE=clean"
    for /f "delims=" %%G in ('git -C "%REPO_ROOT%" status --porcelain 2^>nul') do set "VESTIGIA_MCP_SOURCE_STATE=dirty"
)

if not exist "%TUNNEL_EXE%" (
    echo [VESTIGIA] Tunnel client not found:
    echo   "%TUNNEL_EXE%"
    echo.
    pause
    exit /b 1
)

if not defined CONTROL_PLANE_API_KEY (
    echo [VESTIGIA] CONTROL_PLANE_API_KEY is not set.
    echo.
    echo Set it as a Windows user environment variable, or set it in the
    echo shell before launching this file. The key is intentionally not
    echo stored in this repository or echoed by this launcher.
    echo.
    pause
    exit /b 1
)

if not exist "%VESTIGIA_MCP_LIVE_ARCHIVE_ROOT%" (
    echo [VESTIGIA] Live Archive root not found:
    echo   "%VESTIGIA_MCP_LIVE_ARCHIVE_ROOT%"
    echo.
    pause
    exit /b 1
)

if not exist "%VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT%" (
    echo [VESTIGIA] Snapshot Archive not found:
    echo   "%VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT%"
    echo.
    pause
    exit /b 1
)

echo [VESTIGIA] Lighting the tunnel...
echo   Profile:    %PROFILE%
echo   Live:       %VESTIGIA_MCP_LIVE_ARCHIVE_ROOT%
echo   Snapshot:   %VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT%
echo   Deployment: %VESTIGIA_MCP_DEPLOYMENT_ID%
echo   Source:     %VESTIGIA_MCP_SOURCE_STATE% %VESTIGIA_MCP_SOURCE_COMMIT%
echo.

pushd "%ROOT%"
"%TUNNEL_EXE%" run --profile "%PROFILE%"
set "EXITCODE=%ERRORLEVEL%"
popd

if not "%EXITCODE%"=="0" (
    echo.
    echo [VESTIGIA] Tunnel exited with code %EXITCODE%.
    pause
)

exit /b %EXITCODE%
