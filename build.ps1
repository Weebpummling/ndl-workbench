<#
.SYNOPSIS
Build the NDL Workbench binaries.

.DESCRIPTION
Creates a private virtual environment under desktop\.venv, installs the app's
dependencies there, and packages two one-file executables with PyInstaller:

  "NDL Workbench.exe"    the window (no console)
  ndl-workbench-cli.exe  the same code with --selftest / --fetch, for scripting

The venv is private on purpose. The machine's global Python 3.11 is shared with
other work and pinned to particular numpy/matplotlib versions; nothing here
touches it.

Both binaries carry MANUAL.md, which the app shows in its Manual tab.

.EXAMPLE
.\build.ps1
.EXAMPLE
.\build.ps1 -SkipVenv          # reuse an existing .venv
.EXAMPLE
.\build.ps1 -NoTranslate       # smaller binary, no Anthropic SDK bundled
#>
param(
    [switch]$SkipVenv,
    [switch]$NoTranslate
)
$ErrorActionPreference = 'Stop'

# PyInstaller and pip write progress to stderr. With ErrorActionPreference set
# to Stop, PowerShell turns any native stderr line into a terminating
# NativeCommandError even when the process exits 0 - so every external call
# goes through this helper, which judges success by exit code alone.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$What,
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw "$What failed (exit code $code)" }
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$venv = Join-Path $here '.venv'
$py = Join-Path $venv 'Scripts\python.exe'

if (-not $SkipVenv -or -not (Test-Path $py)) {
    Write-Host '== creating virtual environment' -ForegroundColor Cyan
    Invoke-Native -What 'venv creation' -Exe 'python' -Arguments @('-m', 'venv', $venv)
    Invoke-Native -What 'pip upgrade' -Exe $py -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet')
}

Write-Host '== installing dependencies' -ForegroundColor Cyan
$deps = @('python-docx>=1.1', 'pyinstaller>=6.6')
if (-not $NoTranslate) { $deps += 'anthropic>=1.0' }
Invoke-Native -What 'dependency install' -Exe $py -Arguments (@('-m', 'pip', 'install', '--quiet') + $deps)

Write-Host '== self test (source tree)' -ForegroundColor Cyan
Invoke-Native -What 'source selftest' -Exe $py -Arguments @('-m', 'ndl_workbench', '--selftest')

$dist = Join-Path $here 'dist'
$work = Join-Path $here 'build'
$common = @(
    '--onefile',
    '--clean',
    '--noconfirm',
    '--distpath', $dist,
    '--workpath', $work,
    '--specpath', $work,
    '--add-data', "$here\MANUAL.md;.",
    '--collect-submodules', 'ndl_workbench'
)
if ($NoTranslate) { $common += @('--exclude-module', 'anthropic') }

Write-Host '== packaging the window' -ForegroundColor Cyan
Invoke-Native -What 'GUI build' -Exe $py -Arguments (@('-m', 'PyInstaller') + $common +
    @('--windowed', '--name', 'NDL Workbench', 'run_workbench.py'))

Write-Host '== packaging the console tool' -ForegroundColor Cyan
Invoke-Native -What 'CLI build' -Exe $py -Arguments (@('-m', 'PyInstaller') + $common +
    @('--console', '--name', 'ndl-workbench-cli', 'run_workbench.py'))

Write-Host '== smoke test (packaged)' -ForegroundColor Cyan
Invoke-Native -What 'packaged selftest' -Exe (Join-Path $dist 'ndl-workbench-cli.exe') -Arguments @('--selftest')

Write-Host ''
Write-Host 'built:' -ForegroundColor Green
Get-ChildItem $dist -Filter *.exe | ForEach-Object {
    '{0,-28} {1,8:N1} MB' -f $_.Name, ($_.Length / 1MB)
}
Write-Host ''
Write-Host ('manual: ' + (Join-Path $here 'MANUAL.md'))
