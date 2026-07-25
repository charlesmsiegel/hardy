<#
.SYNOPSIS
    Updates an existing Hardy installation on Windows. No WSL required.

.DESCRIPTION
    Pulls the source tree, reinstalls it so that any newly declared dependency
    is picked up, and runs `hardy doctor`. Run it from PowerShell:

        powershell -ExecutionPolicy Bypass -File scripts\update-windows.ps1

    The install is editable, so new code is live as soon as the source tree
    moves. New dependencies are not, which is the reason this exists: a release
    that adds one otherwise leaves a current checkout and a broken command.

    Mathlib is left alone unless -Mathlib is given. It is a multi-gigabyte
    rebuild and rarely what someone updating Hardy itself wants.

.PARAMETER Yes
    Non-interactive: accept every step.

.PARAMETER Mathlib
    Also run lake update, cache get, and build in the Lean project.

.PARAMETER Toolchain
    Also run elan self update and elan update.

.PARAMETER Source
    The Hardy source tree to pull. Defaults to wherever the installed
    environment says its code lives.

.PARAMETER Prefix
    Where Hardy is installed.
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$Mathlib,
    [switch]$Toolchain,
    [string]$Source = '',
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'hardy')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Venv = Join-Path $Prefix 'venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$LeanProject = Join-Path $Prefix 'lean'

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Detail($message) { Write-Host "    $message" }
function Write-Warn($message) { Write-Warning $message }
function Stop-Update($message) { Write-Host "error: $message" -ForegroundColor Red; exit 1 }
function Test-Command($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

function Confirm-Step($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) { return $true }
    $reply = Read-Host "$question [Y/n]"
    return ($reply -eq '' -or $reply -match '^(y|yes)$')
}

# Ask the installed environment where its own code is, rather than keeping a
# record that could go stale: an editable install resolves the package back to
# the tree it was installed from, clone or fetched copy alike.
function Get-SourceTree {
    if ($Source) {
        if (-not (Test-Path (Join-Path $Source 'pyproject.toml'))) {
            Stop-Update "$Source does not look like the Hardy repository"
        }
        return $Source
    }
    if (-not (Test-Path $VenvPython)) {
        Stop-Update "no Hardy installation at $Prefix; run scripts\install-windows.ps1 first, or pass -Source"
    }
    $found = & $VenvPython -c 'import hardy, pathlib; print(pathlib.Path(hardy.__file__).resolve().parents[2])' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $found -or -not (Test-Path (Join-Path $found 'pyproject.toml'))) {
        Stop-Update "the environment at $Venv does not point at a source tree; pass -Source DIR"
    }
    return $found
}

function Update-Source($tree) {
    Write-Step "Updating the source tree at $tree"
    if (-not (Test-Path (Join-Path $tree '.git'))) {
        # A downloaded archive has no history to pull.
        Write-Warn "$tree is not a git checkout; leaving the code as it is"
        Write-Detail 'run scripts\install-windows.ps1 to fetch a newer copy'
        return
    }
    if (-not (Test-Command 'git')) { Stop-Update 'git is required to update a checkout' }
    $before = (& git -C $tree rev-parse HEAD 2>$null)
    & git -C $tree diff --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "$tree has uncommitted changes"
        if (-not (Confirm-Step 'Pull anyway? Your changes are left in place and may conflict.')) {
            Stop-Update 'stopped: commit or stash your changes, then re-run'
        }
    }
    & git -C $tree pull --ff-only
    if ($LASTEXITCODE -ne 0) { Stop-Update "git pull failed in $tree" }
    $after = (& git -C $tree rev-parse HEAD 2>$null)
    if ($before -eq $after) { Write-Detail "already up to date ($before)" }
    else { Write-Detail "$before -> $after" }
}

# The step that matters: the code is editable and already current, so this is
# what turns a newly declared dependency into an installed one.
function Update-Environment($tree) {
    Write-Step "Reinstalling dependencies into $Venv"
    & $VenvPython -m pip install -e $tree
    if ($LASTEXITCODE -ne 0) { Stop-Update "could not reinstall Hardy into $Venv" }
    Write-Detail 'dependencies are current'
}

function Update-Toolchain {
    if (-not $Toolchain) { return }
    Write-Step 'Updating the Lean toolchain'
    if (-not (Test-Command 'elan')) {
        Write-Warn 'elan is not on PATH; skipping'
        return
    }
    & elan self update
    & elan update
}

function Update-Mathlib {
    if (-not $Mathlib) { return }
    Write-Step "Updating Mathlib in $LeanProject"
    if (-not (Test-Path $LeanProject)) {
        Write-Warn "no Lean project at $LeanProject; skipping"
        return
    }
    if (-not (Test-Command 'lake')) {
        Stop-Update 'lake is not on PATH; open a new terminal, or re-run scripts\install-windows.ps1'
    }
    Write-Detail 'this downloads several gigabytes and typically takes 10-30 minutes'
    Push-Location $LeanProject
    try {
        & lake update
        if ($LASTEXITCODE -ne 0) { Stop-Update 'lake update failed' }
        & lake exe cache get
        if ($LASTEXITCODE -ne 0) { Stop-Update 'lake exe cache get failed' }
        & lake build
        if ($LASTEXITCODE -ne 0) { Stop-Update 'lake build failed' }
    }
    finally { Pop-Location }
    Write-Detail 'Mathlib is current'
}

function Test-Update {
    Write-Step 'Verifying'
    $hardy = Join-Path $Venv 'Scripts\hardy.exe'
    if (-not (Test-Path $hardy)) {
        Write-Warn "the hardy command is missing from $Venv; re-run scripts\install-windows.ps1"
        return
    }
    & $hardy doctor
    if ($LASTEXITCODE -ne 0) { Write-Warn 'hardy doctor reported problems; see above' }
}

$tree = Get-SourceTree
Update-Source $tree
Update-Environment $tree
Update-Toolchain
Update-Mathlib
Test-Update
Write-Host "`nHardy is up to date." -ForegroundColor Green
if (-not $Mathlib) { Write-Detail 'Mathlib was not touched; -Mathlib updates it.' }
