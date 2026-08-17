$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m PyInstaller --clean --noconfirm "$root\packaging\ai_fea_assistant.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with code $LASTEXITCODE" }
    Write-Host "Built: $root\dist\AI-FEA-Valve-Gripper.exe"
}
finally {
    Pop-Location
}
