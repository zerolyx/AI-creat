<#
.SYNOPSIS
    Fetch the CalculiX (ccx) Windows solver runtime into runtime/ccx/.

.DESCRIPTION
    The CalculiX solver binaries are intentionally NOT committed to this
    repository (they would bloat every clone and make history unreviewable).
    Run this script once before using or packaging AI-FEA Valve Gripper.

    Source: pre-built Windows package published by the CalculiX community
    project (calculix/CalculiX-Windows), pinned to a specific release so
    downloads are reproducible.

.NOTES
    Requires: Windows 10+, PowerShell 5.1+, an internet connection.
    Idempotent: re-running after a successful fetch is a no-op (skips
    re-downloading). Use -Force to overwrite an existing runtime.
#>
[CmdletBinding()]
param(
    # Re-download and overwrite files even if runtime/ccx/ccx.exe already exists.
    [switch]$Force,

    # Override the default CalculiX Windows package download URL.
    [string]$ReleaseUrl = 'https://raw.githubusercontent.com/calculix/CalculiX-Windows/master/releases/CalculiX-2.23.0-win-x64.zip'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$CcxDir   = Join-Path (Join-Path $RepoRoot 'runtime') 'ccx'
$TargetExe = Join-Path $CcxDir 'ccx.exe'

Write-Host '==> AI-FEA Valve Gripper: fetching CalculiX solver runtime' -ForegroundColor Cyan

# --- Already present? fast path ---
if (Test-Path $TargetExe) {
    if (-not $Force) {
        Write-Host "  ccx.exe already present: $TargetExe" -ForegroundColor Green
        Write-Host '  Re-run with -Force to re-download.' -ForegroundColor DarkGray
        exit 0
    }
    Write-Host '  -Force set: re-downloading.' -ForegroundColor Yellow
}

# --- Download (curl.exe is bundled with Windows 10/11; fall back to Invoke-WebRequest) ---
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('ai-fea-ccx-' + [guid]::NewGuid().ToString('N'))
$ZipPath  = Join-Path $TempRoot 'ccx.zip'
$ExtractDir = Join-Path $TempRoot 'extract'
$null = New-Item -ItemType Directory -Path $TempRoot, $ExtractDir -Force

try {
    # A local path (offline / air-gapped installs) is used as-is.
    $LocalZip = Test-Path -LiteralPath $ReleaseUrl
    if ($LocalZip) {
        Copy-Item -LiteralPath $ReleaseUrl -Destination $ZipPath -Force
        Write-Host "  Using local package: $ReleaseUrl" -ForegroundColor Cyan
    }
    else {
        Write-Host "  Downloading $($ReleaseUrl)" -ForegroundColor Cyan
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe -L --fail --silent --show-error -o $ZipPath $ReleaseUrl
            if ($LASTEXITCODE -ne 0) { throw "curl.exe failed with exit code $LASTEXITCODE ($ReleaseUrl)" }
        }
        else {
            Invoke-WebRequest -Uri $ReleaseUrl -OutFile $ZipPath -UseBasicParsing
        }
    }
    $zipBytes = (Get-Item $ZipPath).Length
    Write-Host ("  Package {0:N1} MB" -f ($zipBytes / 1MB)) -ForegroundColor Green

    # --- Extract only the solver runtime (bin/*) ---
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
    $BinDir = Get-ChildItem -Path $ExtractDir -Recurse -Directory -Filter 'bin' |
        Select-Object -First 1
    if (-not $BinDir) { throw "CalculiX package does not contain a bin/ directory: $ReleaseUrl" }

    $RuntimeFiles = @(
        'ccx.exe',
        'glut64.dll',
        'libgcc_s_seh-1.dll',
        'libgfortran-3.dll',
        'libgomp-1.dll',
        'libquadmath-0.dll',
        'libstdc++-6.dll',
        'libwinpthread-1.dll',
        'pthreadGC2.dll',
        'LICENSE.txt'
    )

    $null = New-Item -ItemType Directory -Path $CcxDir -Force
    foreach ($file in $RuntimeFiles) {
        $src = Join-Path $BinDir.FullName $file
        if (-not (Test-Path $src)) {
            Write-Warning "  missing in package: $file (skipped)"
            continue
        }
        Copy-Item -Path $src -Destination (Join-Path $CcxDir $file) -Force
    }

    if (-not (Test-Path $TargetExe)) {
        throw 'ccx.exe was not produced by the fetch. The package layout may have changed.'
    }

    Write-Host "  Installed to: $CcxDir" -ForegroundColor Green
    Get-ChildItem $CcxDir -File | ForEach-Object { Write-Host ("    {0}  ({1:N0} bytes)" -f $_.Name, $_.Length) -ForegroundColor DarkGray }
    Write-Host '==> Done. You can now run run_gui.py / run_mvp.py or build_exe.ps1.' -ForegroundColor Green
}
finally {
    if (Test-Path $TempRoot) { Remove-Item $TempRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
