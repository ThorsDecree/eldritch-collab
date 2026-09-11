@echo off
rem Copy this file to "Start VESTIGIA MCP Tunnel.local.bat" and replace the placeholder.
rem The .local.bat filename is ignored by Git and loaded by the main tunnel launcher.
set "CONTROL_PLANE_API_KEY=PASTE_CONTROL_PLANE_API_KEY_HERE"

rem Optional machine-local overrides may also live here. The checked-in launcher already
rem supplies Jeff's current relative Archive, Runtime Home, write grants, and byte ceilings.
rem set "VESTIGIA_MCP_DEPLOYMENT_ID=jeff-desktop"
