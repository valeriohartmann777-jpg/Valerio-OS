# Start JARVIS in development mode (Vite + Electron; Electron starts the backend).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
npm run dev
