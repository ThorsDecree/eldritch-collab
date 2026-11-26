[CmdletBinding()]
param(
    [string]$Profile,
    [Alias("Home")]
    [string]$RuntimeHome,
    [string]$RuntimeEnvFile,
    [ValidateSet("Web", "Discord")]
    [string]$Mode = "Web",
    [ValidateRange(1, 65535)]
    [int]$WebPort = 8767,
    [string]$McpLauncher,
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$runtimeRoot = Join-Path $PSScriptRoot "VESTIGIA_Runtime"
$mcpRoot = Join-Path $PSScriptRoot "VESTIGIA_MCP_Server"
$runtimeLauncher = Join-Path $runtimeRoot "Start VESTIGIA.cmd"

if (!$PrepareOnly) {
    if ($Profile -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
        throw "Supply an existing tunnel profile with -Profile."
    }
    if (!$RuntimeHome -or !$RuntimeEnvFile) {
        throw "Supply the pilot home and environment file with -RuntimeHome and -RuntimeEnvFile."
    }
    $selectedHome = (Resolve-Path -LiteralPath $RuntimeHome).ProviderPath
    if (!(Test-Path -LiteralPath (Join-Path $selectedHome "home.yaml") -PathType Leaf)) {
        throw "The selected Runtime home is missing home.yaml."
    }
    $selectedEnv = (Resolve-Path -LiteralPath $RuntimeEnvFile).ProviderPath
    if (!(Test-Path -LiteralPath $selectedEnv -PathType Leaf)) {
        throw "The Runtime environment must be a file."
    }
    $builtInLauncher = !$McpLauncher
    if ($builtInLauncher) {
        $McpLauncher = Join-Path $mcpRoot "Start VESTIGIA MCP Tunnel.bat"
    }
    $McpLauncher = (Resolve-Path -LiteralPath $McpLauncher).ProviderPath
    if (!(Test-Path -LiteralPath $McpLauncher -PathType Leaf) -or
        [IO.Path]::GetExtension($McpLauncher) -notin @(".bat", ".cmd")) {
        throw "The MCP launcher must be a Windows batch or command file."
    }
}

& $runtimeLauncher --prepare
if ($LASTEXITCODE -ne 0) {
    throw "Runtime environment preparation failed."
}

$mcpVenv = Join-Path $mcpRoot ".venv"
$mcpPython = Join-Path $mcpVenv "Scripts\python.exe"
$mcpCommand = Join-Path $mcpVenv "Scripts\vestigia-mcp.exe"
if (!(Test-Path -LiteralPath $mcpPython -PathType Leaf)) {
    & py -3 -m venv $mcpVenv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the MCP Server virtual environment."
    }
}
$probe = "import sys; from pathlib import Path; import vestigia, vestigia_mcp.cli; assert Path(vestigia_mcp.cli.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve() / 'src'); assert Path(vestigia.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve() / 'src')"
& $mcpPython -I -c $probe $mcpRoot $runtimeRoot *> $null
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $mcpCommand -PathType Leaf)) {
    & $mcpPython -I -m pip install -e $mcpRoot -e $runtimeRoot
    if ($LASTEXITCODE -ne 0) {
        throw "MCP Server environment preparation failed."
    }
    & $mcpPython -I -c $probe $mcpRoot $runtimeRoot
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $mcpCommand -PathType Leaf)) {
        throw "MCP Server or Runtime projection imports are unavailable after setup."
    }
}
if ($PrepareOnly) {
    Write-Host "[VESTIGIA] Both environments are ready."
    return
}

# Apply launch variables only when starting the tunnel. Its local sidecar is loaded
# in that child process, so its control-plane key is never copied into this session.
$tunnelEnvironment = @{
    VESTIGIA_SUITE_RUNTIME_HOME = $selectedHome
    VESTIGIA_SUITE_RUNTIME_ENV_FILE = $selectedEnv
    VESTIGIA_MCP_RUNTIME_HOME = $selectedHome
    VESTIGIA_MCP_RUNTIME_ENV_FILE = $selectedEnv
    VESTIGIA_SUITE_MCP_LAUNCHER = $McpLauncher
    VESTIGIA_SUITE_TUNNEL_PROFILE = $Profile
}
$previous = @{}
foreach ($name in $tunnelEnvironment.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
try {
    foreach ($name in $tunnelEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $tunnelEnvironment[$name], "Process")
    }
    if ($builtInLauncher) {
        Push-Location (Split-Path -Parent $McpLauncher)
        try {
            & $McpLauncher $Profile --check-config
            if ($LASTEXITCODE -ne 0) {
                throw "MCP tunnel configuration check failed."
            }
        } finally {
            Pop-Location
        }
    }
    Start-Process -FilePath $env:ComSpec -WorkingDirectory (Split-Path -Parent $McpLauncher) `
        -ArgumentList '/d /v:off /s /k ""%VESTIGIA_SUITE_MCP_LAUNCHER%" "%VESTIGIA_SUITE_TUNNEL_PROFILE%""'
} finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
    }
}

if ($Mode -eq "Discord") {
    & $runtimeLauncher discord $selectedHome --env-file $selectedEnv
} else {
    & $runtimeLauncher web --home $selectedHome --env-file $selectedEnv --port $WebPort
}
exit $LASTEXITCODE
