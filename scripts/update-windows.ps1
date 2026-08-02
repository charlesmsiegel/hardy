<#
.SYNOPSIS
    Updates an existing Hardy installation on Windows. No WSL required.

.DESCRIPTION
    Updates Hardy in place and runs `hardy doctor`. Run it from PowerShell:

        powershell -ExecutionPolicy Bypass -File scripts\update-windows.ps1

    There are two kinds of installation and this updates either. An install made
    from the published release has no source tree: it downloads the newest
    released wheel, checks it against the release manifest, and installs that.
    An install made from a checkout is editable, so new code is live as soon as
    the tree moves; new dependencies are not, which is the reason that path
    exists — a release that adds one otherwise leaves a current checkout and a
    broken command.

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
$RepoUrl = if ($env:HARDY_REPO_URL) { $env:HARDY_REPO_URL } else { 'https://github.com/charlesmsiegel/hardy' }
$ReleaseVersion = if ($env:HARDY_VERSION) { $env:HARDY_VERSION } else { '' }

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

# These four mirror install-windows.ps1. They are duplicated rather than shared
# because install-windows.ps1 must run as a single downloaded file, with no
# scripts\lib beside it to dot-source; the test suite checks that both copies
# still verify what they download.
$ReleaseOrigin = Join-Path $Prefix 'release-origin'

# The repository this installation's releases come from, recorded by the
# installer: without it, updating a fork's installation would quietly move it to
# the official Hardy repository.
function Get-ReleaseRepoUrl {
    if ($env:HARDY_REPO_URL) { return $env:HARDY_REPO_URL }
    if (Test-Path -LiteralPath $ReleaseOrigin) {
        foreach ($line in [System.IO.File]::ReadAllLines($ReleaseOrigin)) {
            if ($line.StartsWith('repo=')) { return $line.Substring(5).Trim() }
        }
    }
    return $RepoUrl
}

function Get-ReleaseBaseUrl {
    if ($env:HARDY_RELEASE_BASE_URL) { return $env:HARDY_RELEASE_BASE_URL.TrimEnd('/') }
    $repository = Get-ReleaseRepoUrl
    if ($ReleaseVersion) { return "$repository/releases/download/$ReleaseVersion" }
    return "$repository/releases/latest/download"
}

function Find-ReleaseAsset($manifest, $suffix) {
    foreach ($line in [System.IO.File]::ReadAllLines($manifest)) {
        $fields = $line.Trim() -split '\s+', 2
        if ($fields.Count -ne 2) { continue }
        $name = $fields[1].Trim().TrimStart('*')
        if ($name.EndsWith($suffix)) { return @{ Digest = $fields[0]; Name = $name } }
    }
    return $null
}

function Save-ReleaseAsset($suffix, $directory) {
    $base = Get-ReleaseBaseUrl
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $manifest = Join-Path $directory 'SHA256SUMS'
    # One manifest for both artifacts, so an update cannot take its installers
    # from one release and its wheel from the next.
    if ($env:HARDY_RELEASE_MANIFEST -and (Test-Path -LiteralPath $env:HARDY_RELEASE_MANIFEST)) {
        Copy-Item -LiteralPath $env:HARDY_RELEASE_MANIFEST -Destination $manifest -Force
    }
    else {
        try { Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile $manifest -UseBasicParsing }
        catch { Stop-Update "could not fetch $base/SHA256SUMS - is there a published release yet? (HARDY_VERSION selects one)" }
    }
    $asset = Find-ReleaseAsset $manifest $suffix
    if (-not $asset) { Stop-Update "the release at $base has no $suffix asset" }
    $path = Join-Path $directory $asset.Name
    try { Invoke-WebRequest -Uri "$base/$($asset.Name)" -OutFile $path -UseBasicParsing }
    catch { Stop-Update "could not download $base/$($asset.Name)" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower()
    if ($actual -ne $asset.Digest.ToLower()) {
        Stop-Update "checksum mismatch for $($asset.Name): the release says $($asset.Digest), the download is $actual"
    }
    return $path
}

# Ask the installed environment where its own code is, rather than keeping a
# record that could go stale: an editable install resolves the package back to
# the tree it was installed from. A release install resolves to no tree at all,
# and that absence is the answer rather than a fault to report.
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
    # Read from the distribution's own metadata, not by importing Hardy: a
    # checkout that cannot be imported right now -- a syntax error mid-edit, a
    # dependency not yet installed -- is still an editable install, and
    # answering "release" for it would replace the developer's checkout with a
    # published wheel.
    $probe = @'
import json, pathlib
from importlib import metadata
from urllib.parse import urlparse
from urllib.request import url2pathname

try:
    distribution = metadata.distribution("hardy-prover")
except metadata.PackageNotFoundError:
    raise SystemExit(1)

# pip records this for anything installed from a local path or a URL; a wheel
# install from a file has one too, so `editable` is what distinguishes them.
recorded = distribution.read_text("direct_url.json")
if recorded:
    direct = json.loads(recorded)
    parsed = urlparse(direct.get("url", ""))
    if direct.get("dir_info", {}).get("editable") and parsed.scheme == "file":
        # url2pathname, not a slice: the path is percent-encoded, and on
        # Windows it carries a leading slash before the drive letter.
        tree = pathlib.Path(url2pathname(parsed.path))
        if (tree / "pyproject.toml").is_file():
            print(tree)
            raise SystemExit(0)
raise SystemExit(3)
'@
    $found = ($probe | & $VenvPython -) 2>$null
    if ($LASTEXITCODE -eq 0 -and $found) { return $found.Trim() }
    if ($LASTEXITCODE -eq 3) { return '' }
    Stop-Update "the environment at $Venv has no Hardy in it to update; re-run scripts\install-windows.ps1, or pass -Source DIR"
}

# There is no source tree here: the wheel named by the release manifest is both
# the new code and the new dependency list.
function Update-FromRelease {
    Write-Step "Updating Hardy from $(Get-ReleaseBaseUrl)"
    $directory = Join-Path $Prefix 'download'
    Remove-Item -Recurse -Force $directory -ErrorAction SilentlyContinue
    # Both artifacts in hand, verified, before either is put in place: a bundle
    # that fails to download after the wheel had already moved would leave
    # release N's updater and uninstaller minding release N+1.
    $staged = Request-Installers
    $wheel = Save-ReleaseAsset '.whl' $directory
    Write-Detail "verified $(Split-Path -Leaf $wheel) against the release manifest"
    # --upgrade, not --force-reinstall: a published release is never rewritten
    # (the release workflow refuses to replace the assets of one), so the
    # version in the wheel's name is the whole answer to whether there is
    # anything to do here.
    & $VenvPython -m pip install --upgrade $wheel
    if ($LASTEXITCODE -ne 0) { Stop-Update "could not install $(Split-Path -Leaf $wheel) into $Venv" }
    Write-Detail "installed $(Split-Path -Leaf $wheel)"
    Remove-Item -Recurse -Force $directory -ErrorAction SilentlyContinue
    Complete-Installers $staged
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

# The installers a release install keeps are what updates and removes it later,
# so they move with the wheel: after an update to release N+1, release N's
# uninstaller would otherwise be the one that runs. Downloaded and verified
# first, put in place last.
function Request-Installers {
    $installers = Join-Path $Prefix 'installers'
    if (-not (Test-Path $installers)) { return '' }
    if (-not (Test-Command 'tar')) {
        Write-Warn "tar is not available; $installers still holds the previous release's scripts"
        return ''
    }
    Write-Step 'Fetching the installers for this release'
    $staging = Join-Path $Prefix 'installers.new'
    Remove-Item -Recurse -Force $staging, (Join-Path $Prefix 'installers.previous') -ErrorAction SilentlyContinue
    $bundle = Save-ReleaseAsset 'hardy-installers.tar.gz' (Join-Path $staging 'download')
    $tree = Join-Path $staging 'tree'
    New-Item -ItemType Directory -Force -Path $tree | Out-Null
    & tar -xzf $bundle -C $tree
    if ($LASTEXITCODE -ne 0) { Stop-Update "could not unpack $(Split-Path -Leaf $bundle)" }
    if (-not (Test-Path (Join-Path $tree 'scripts\install-windows.ps1'))) {
        Stop-Update "$(Split-Path -Leaf $bundle) does not carry the Hardy installers"
    }
    Write-Detail "verified $(Split-Path -Leaf $bundle) against the release manifest"
    $env:HARDY_RELEASE_MANIFEST = Join-Path $staging 'download\SHA256SUMS'
    return $tree
}

# Swapped whole. A half-written installers directory is worse than an old one.
function Complete-Installers($tree) {
    if (-not $tree) { return }
    $installers = Join-Path $Prefix 'installers'
    $previous = Join-Path $Prefix 'installers.previous'
    Move-Item $installers $previous
    Move-Item $tree $installers
    Remove-Item -Recurse -Force (Join-Path $Prefix 'installers.new'), $previous -ErrorAction SilentlyContinue
    Write-Detail 'the installers now match the installed release'
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
if ($tree) {
    Update-Source $tree
    Update-Environment $tree
}
else {
    Update-FromRelease
}
Update-Toolchain
Update-Mathlib
Test-Update
Write-Host "`nHardy is up to date." -ForegroundColor Green
if (-not $Mathlib) { Write-Detail 'Mathlib was not touched; -Mathlib updates it.' }
