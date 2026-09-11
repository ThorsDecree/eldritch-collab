@echo off
setlocal
title VESTIGIA MCP Tunnel

set "ROOT=%~dp0"
set "REPO_ROOT=%ROOT%.."
set "TUNNEL_EXE=%ROOT%tunnel-client-v0.0.14-windows-amd64\tunnel-client.exe"
set "PROFILE=vestigia-local"
if not "%~1"=="" set "PROFILE=%~1"

rem Load secrets and machine-local overrides without putting them in Git.
rem Copy the checked-in .local.example.bat file to .local.bat and add the API key there.
set "LOCAL_ENV=%ROOT%Start VESTIGIA MCP Tunnel.local.bat"
if exist "%LOCAL_ENV%" call "%LOCAL_ENV%"

if not defined VESTIGIA_MCP_LIVE_ARCHIVE_ROOT for %%I in ("%REPO_ROOT%\VESTIGIA") do set "VESTIGIA_MCP_LIVE_ARCHIVE_ROOT=%%~fI"
if not defined VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT for %%I in ("%REPO_ROOT%\VESTIGIA\Anima.zip") do set "VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT=%%~fI"
if not defined VESTIGIA_MCP_STATE_DIR set "VESTIGIA_MCP_STATE_DIR=%USERPROFILE%\.vestigia-mcp"
if not defined VESTIGIA_MCP_DEPLOYMENT_ID set "VESTIGIA_MCP_DEPLOYMENT_ID=jeff-desktop"
if not defined VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES set "VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES=1000000"
if not defined VESTIGIA_MCP_ARCHIVE_MEDIA_MAX_BYTES set "VESTIGIA_MCP_ARCHIVE_MEDIA_MAX_BYTES=20000000"
if not defined VESTIGIA_MCP_ARCHIVE_WRITE_MAX_BYTES set "VESTIGIA_MCP_ARCHIVE_WRITE_MAX_BYTES=1000000"
if not defined VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES set "VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES=02_Journal,04_Payloads,06_Structures,07_Labs,08_Exports,Anima,Bubbles,Cow,Friction,Inkling,Isabel_and_Indexia,Jeff,JeffPrime,Kael,Liora,MB,Palim,Rain,Sable,Seryn,Sol,Sphinx,Viv"

if not defined VESTIGIA_MCP_RUNTIME_HOME for %%I in ("%REPO_ROOT%\VESTIGIA_Runtime\homes\liora") do set "VESTIGIA_MCP_RUNTIME_HOME=%%~fI"
if not defined VESTIGIA_MCP_RUNTIME_ENV_FILE (
    if exist "%REPO_ROOT%\VESTIGIA_Runtime\.env" (
        for %%I in ("%REPO_ROOT%\VESTIGIA_Runtime\.env") do set "VESTIGIA_MCP_RUNTIME_ENV_FILE=%%~fI"
    )
)
if not defined VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS set "VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS=fs.stage_patch,file.write,file.patch,fs.patch_discard"

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
    echo Copy "Start VESTIGIA MCP Tunnel.local.example.bat" to
    echo "Start VESTIGIA MCP Tunnel.local.bat" and add the key there,
    echo or set it as a Windows user/shell environment variable.
    echo The key is intentionally not stored in Git or echoed here.
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

if not exist "%VESTIGIA_MCP_RUNTIME_HOME%\home.yaml" (
    echo [VESTIGIA] Runtime Home not found or missing home.yaml:
    echo   "%VESTIGIA_MCP_RUNTIME_HOME%"
    echo.
    pause
    exit /b 1
)

echo [VESTIGIA] Lighting the tunnel...
echo   Profile:    %PROFILE%
echo   Live:       %VESTIGIA_MCP_LIVE_ARCHIVE_ROOT%
echo   Snapshot:   %VESTIGIA_MCP_SNAPSHOT_ARCHIVE_ROOT%
echo   Runtime:    %VESTIGIA_MCP_RUNTIME_HOME%
echo   Deployment: %VESTIGIA_MCP_DEPLOYMENT_ID%
echo   Source:     %VESTIGIA_MCP_SOURCE_STATE% %VESTIGIA_MCP_SOURCE_COMMIT%
echo   Text max:   %VESTIGIA_MCP_ARCHIVE_TEXT_MAX_BYTES% bytes
echo   Media max:  %VESTIGIA_MCP_ARCHIVE_MEDIA_MAX_BYTES% bytes
if defined VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS (
    echo   Runtime writes: %VESTIGIA_MCP_RUNTIME_WRITE_ACTIONS%
) else (
    echo   Runtime writes: disabled
)
if defined VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES (
    echo   Archive writes: %VESTIGIA_MCP_ARCHIVE_WRITE_PREFIXES%
) else (
    echo   Archive writes: disabled
)
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
