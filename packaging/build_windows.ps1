<#
.SYNOPSIS
    Build PersonSort as a standalone --onedir bundle + Windows Installer (Inno Setup).
.EXAMPLE
    # Clean build with CPU-only PyTorch (saves ~2-3 GB) + Installer:
    .\packaging\build_windows.ps1 -CreateCpuVenv

    # Reuse existing env and pre-bundle downloaded models:
    .\packaging\build_windows.ps1 -PrebundleModels -Version 1.0.0

    # Re-compile only the Inno Setup installer from existing packaging\dist\PersonSort:
    .\packaging\build_windows.ps1 -InstallerOnly -Version 1.0.0
#>
param(
    [switch]$CreateCpuVenv,
    [switch]$PrebundleModels,
    [switch]$InstallerOnly,
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$PackagingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $PackagingDir
$SrcDir = Join-Path $RepoRoot "PersonSort"
$BuildEnv = Join-Path $PackagingDir "build_env"
$DistDir = Join-Path $PackagingDir "dist"
$WorkDir = Join-Path $PackagingDir "build"

if (-not $InstallerOnly) {
    if ($CreateCpuVenv) {
        Write-Host "Creating clean CPU-only build virtual environment at $BuildEnv..."
        python -m venv $BuildEnv
        $PythonExe = Join-Path $BuildEnv "Scripts\python.exe"
        & $PythonExe -m pip install --upgrade pip
        # Force CPU-only PyTorch first to save ~2-3 GB vs CUDA wheels
        & $PythonExe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
        & $PythonExe -m pip install -r (Join-Path $SrcDir "requirements.txt")
        & $PythonExe -m pip install pyinstaller
    } elseif (Test-Path (Join-Path $BuildEnv "Scripts\python.exe")) {
        $PythonExe = Join-Path $BuildEnv "Scripts\python.exe"
    } else {
        $PythonExe = Join-Path $SrcDir "env\Scripts\python.exe"
        if (-not (Test-Path $PythonExe)) {
            throw "No Python build environment found. Re-run with -CreateCpuVenv."
        }
        # Ensure pyinstaller is installed in the build Python
        & $PythonExe -c "import PyInstaller" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Installing pyinstaller into $PythonExe..."
            & $PythonExe -m pip install pyinstaller
        }
    }

    if ($PrebundleModels) {
        $env:PERSONSORT_PREBUNDLE_MODELS = "1"
    } else {
        $env:PERSONSORT_PREBUNDLE_MODELS = "0"
    }

    Write-Host "Building PersonSort (--onedir, --noconsole) with PyInstaller..."
    & $PythonExe -m PyInstaller --noconfirm --clean `
        --distpath $DistDir `
        --workpath $WorkDir `
        (Join-Path $PackagingDir "PersonSort.spec")

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
}

$IsccCandidates = @(
    @(
        (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }
)

if ($IsccCandidates.Count -gt 0) {
    $IsccExe = [string]$IsccCandidates[0]
    Write-Host "Compiling Windows installer via Inno Setup ($IsccExe)..."
    & $IsccExe "/DMyAppVersion=$Version" (Join-Path $PackagingDir "PersonSort.iss")
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed."
    }
    Write-Host "Done! Installer saved under $(Join-Path $PackagingDir 'Output')"
} else {
    Write-Host "PyInstaller bundle complete at: $(Join-Path $DistDir 'PersonSort')"
    Write-Host "Note: Inno Setup 6 (ISCC.exe) not found. Install Inno Setup 6 and run ISCC on packaging\PersonSort.iss to create Setup.exe."
}
