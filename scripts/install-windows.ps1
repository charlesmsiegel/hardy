<#
.SYNOPSIS
    One-shot Hardy install for Windows. No WSL required.

.DESCRIPTION
    Takes a clean Windows machine to a working `hardy` command: Python, the Lean
    toolchain (elan/lake), a Mathlib project, Tectonic, and Hardy itself. Run it
    from PowerShell, in a clone or on its own:

        powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

    No clone is required. Run from a checkout it installs that tree, editable;
    run on its own it downloads Hardy's published wheel, checks it against the
    release manifest, and installs that. Winget installs Python, Git, and
    optional full TeX Live; elan and Tectonic use pinned, digest-checked releases.

.PARAMETER Yes
    Non-interactive: accept every install and skip the configuration prompts.

.PARAMETER FromRelease
    Install the published wheel even when run from a checkout.

.PARAMETER FromSource
    Install this source tree, editable. The default when run from a checkout.

.PARAMETER SkipMathlib
    Install lake but do not create or build the shared Mathlib project.

.PARAMETER SkipLatex
    Do not install a TeX distribution.

.PARAMETER FullLatex
    Also install full TeX Live providing pdflatex instead of using Tectonic for interactive cells.

.PARAMETER NoConfig
    Do not write the config file.

.PARAMETER NoLauncher
    Do not put a Hardy launcher on the Desktop and in the Start Menu. The
    launcher runs `hardy web --open` in a console window; Ctrl+C there stops
    the server. The Start Menu entry is what makes "Pin to taskbar" a
    right-click away, since Windows lets no script pin.

.PARAMETER Prefix
    Where Hardy keeps its virtual environment and Lean project.

.PARAMETER BinDir
    Where the `hardy` command is placed and added to your user PATH.
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$SkipMathlib,
    [switch]$SkipLatex,
    [switch]$FullLatex,
    [switch]$NoConfig,
    [switch]$NoLauncher,
    [switch]$FromRelease,
    [switch]$FromSource,
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'hardy'),
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA 'hardy\bin')
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

# Captured here, at script scope, because $PSBoundParameters inside a function
# is that function's own — empty for one that takes no parameters. Handing over
# to the release's installer with this lost would silently drop -Yes, and with
# it -SkipMathlib, and start a multi-gigabyte Mathlib build nobody asked for.
$ScriptParameters = $PSBoundParameters

$RepoRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
$Venv = Join-Path $Prefix 'venv'
$LeanProject = Join-Path $Prefix 'lean'
$LeanPackage = 'hardymath'
# Pinned by identity; scripts/lib/common.sh and src/hardy/installers.py carry
# the same two values, and tests/test_install_scripts.py holds them together.
$LeanToolchain = 'leanprover/lean4:v4.35.0-rc3'
$MathlibRevision = '5e0c4e5239cb0a2d86d68a884bf52cfd963fce22'
$ConfigPath = if ($env:HARDY_CONFIG) { $env:HARDY_CONFIG } else { Join-Path $HOME '.hardy\config.toml' }
$ConfiguredModel = ''
$Python = ''
$RepoUrl = if ($env:HARDY_REPO_URL) { $env:HARDY_REPO_URL } else { 'https://github.com/charlesmsiegel/hardy' }
# Which release to install: a tag, or empty for whatever is current.
$ReleaseVersion = if ($env:HARDY_VERSION) { $env:HARDY_VERSION } else { '' }
# 'release' downloads the published wheel and needs no source tree at all;
# 'source' installs the tree this script came from, editable. Resolve-InstallSource
# decides between them from what is actually here.
$InstallFrom = ''

function Hide-Credentials($message) { [string]$message -replace '://[^/@\s]*@', '://***@' }

function Write-Step($message) { Write-Host (Hide-Credentials "`n==> $message") -ForegroundColor Cyan }
function Write-Detail($message) { Write-Host (Hide-Credentials "    $message") }
function Write-Warn($message) { Write-Warning (Hide-Credentials $message) }
function Stop-Install($message) { Write-Host (Hide-Credentials "error: $message") -ForegroundColor Red; exit 1 }
function Test-Command($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

# Under Windows PowerShell 5.1 (and 7.0/7.1), redirecting a native command's
# error stream turns each line it writes there into a terminating
# NativeCommandError when $ErrorActionPreference = 'Stop', regardless of the
# command's exit code, and regardless of what the redirect sends the text to.
# PowerShell 7.2 fixed this (PSNotApplyErrorActionToStderr), but 5.1 is the
# documented entry point (`powershell -ExecutionPolicy Bypass -File ...`), so
# every native call whose stderr this script wants to ignore or capture goes
# through here instead of a bare redirect. Only $LASTEXITCODE decides success;
# stderr is never fatal by itself. (#301)
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$File,
        [string[]]$Arguments = @(),
        $InputObject,
        [switch]$Quiet,
        [switch]$DropErrors,
        [switch]$CaptureErrors
    )
    $ErrorActionPreference = 'Continue'
    if ($Quiet) {
        if ($PSBoundParameters.ContainsKey('InputObject')) { $InputObject | & $File @Arguments *> $null }
        else { & $File @Arguments *> $null }
    }
    elseif ($CaptureErrors) { & $File @Arguments 2>&1 }
    elseif ($DropErrors) {
        if ($PSBoundParameters.ContainsKey('InputObject')) { $InputObject | & $File @Arguments 2> $null }
        else { & $File @Arguments 2> $null }
    }
    else {
        if ($PSBoundParameters.ContainsKey('InputObject')) { $InputObject | & $File @Arguments }
        else { & $File @Arguments }
    }
}

# Windows PowerShell's `-Encoding UTF8` prepends a byte-order mark, and the two
# readers of these files both choke on one: Lean reports "expected token" before
# `import`, and tomllib "Invalid statement" before the first key. Everything
# generated here is written through these instead.
function Write-Utf8File($path, $text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))
}

function Add-Utf8Line($path, $line) {
    $existing = if (Test-Path $path) { [System.IO.File]::ReadAllText($path) } else { '' }
    if ($existing -and -not $existing.EndsWith("`n")) { $existing += "`r`n" }
    Write-Utf8File $path ($existing + $line + "`r`n")
}

function Confirm-Step($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) { return $true }
    $reply = Read-Host "$question [Y/n]"
    return ($reply -eq '' -or $reply -match '^(y|yes)$')
}

# Winget and the installers it runs change PATH for future processes only.
# Compare complete entries after expansion; preserve every unrelated raw entry.
function Test-PathEntry($pathValue, $directory) {
    $want = [Environment]::ExpandEnvironmentVariables($directory).TrimEnd('\')
    foreach ($entry in ($pathValue -split ';')) {
        if ($entry -and [Environment]::ExpandEnvironmentVariables($entry).TrimEnd('\') -ieq $want) { return $true }
    }
    return $false
}

function Get-UserPathRaw {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    try {
        $value = $key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $kind = if ($key.GetValueNames() -contains 'Path') { $key.GetValueKind('Path') } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
        return @{ Value = $value; Kind = $kind }
    }
    finally { $key.Close() }
}

function Set-UserPathRaw($value, $kind) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    try { $key.SetValue('Path', $value, $kind) }
    finally { $key.Close() }
    [Environment]::SetEnvironmentVariable('HARDY_PATH_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('HARDY_PATH_REFRESH', $null, 'User')
}

function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
    foreach ($extra in @((Join-Path $env:USERPROFILE '.elan\bin'), $BinDir)) {
        if ((Test-Path $extra) -and (-not (Test-PathEntry $env:Path $extra))) { $env:Path = "$extra;$env:Path" }
    }
}

function Install-WithWinget($id, $description) {
    if (-not (Test-Command 'winget')) {
        Stop-Install "winget is not available, so $description cannot be installed automatically. Install App Installer from the Microsoft Store, or install $description by hand, then re-run this script."
    }
    Write-Detail "winget install $id"
    $arguments = @('install', '--id', $id, '--exact', '--source', 'winget',
        '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
    & winget @arguments
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
        Stop-Install "winget failed to install $id (exit $LASTEXITCODE)"
    }
    Update-SessionPath
}

function Get-Python {
    foreach ($candidate in @('python3.13', 'python3.12', 'python3.11', 'python', 'python3')) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        # The Windows Store alias is a stub that exits without running Python.
        Invoke-Native $command.Source @('-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)') -Quiet
        if ($LASTEXITCODE -eq 0) { return $command.Source }
    }
    return $null
}

# --- releases ---------------------------------------------------------------
#
# Installing Hardy means putting a released wheel into a virtual environment,
# not obtaining a copy of the repository. Nothing downloaded here is used before
# its digest has been checked against the release's own manifest.

# Which repository this installation's releases come from. Recorded at install
# time so that an install made from a fork is updated from that fork: the
# updater running later has none of the environment the installer was given.
$ReleaseOrigin = Join-Path $Prefix 'release-origin'

# The repository actually used, not the variable's default: re-running the
# retained installer on a fork's installation resolves to that fork, and writing
# $RepoUrl here would replace the record with the official repository.
function Save-ReleaseOrigin {
    New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
    Write-Utf8File $ReleaseOrigin "repo=$(Get-ReleaseRepoUrl)`r`n"
}

# Chosen now, else whatever this installation was made from, else Hardy's own.
function Get-ReleaseRepoUrl {
    if ($env:HARDY_REPO_URL) { return $env:HARDY_REPO_URL }
    if (Test-Path -LiteralPath $ReleaseOrigin) {
        foreach ($line in [System.IO.File]::ReadAllLines($ReleaseOrigin)) {
            if ($line.StartsWith('repo=')) { return $line.Substring(5).Trim() }
        }
    }
    return $RepoUrl
}

# A re-run of the retained installer meets an environment that already has a
# wheel in it. "Already the same version" is only an answer while the wheels come
# from the same place: moving an installation to a fork whose wheel carries the
# same version is a different wheel under the same number, and pip would report
# success and change nothing while the origin record and the installers moved.
function Get-ReinstallArguments {
    $recorded = ''
    if (Test-Path -LiteralPath $ReleaseOrigin) {
        foreach ($line in [System.IO.File]::ReadAllLines($ReleaseOrigin)) {
            if ($line.StartsWith('repo=')) { $recorded = $line.Substring(5).Trim() }
        }
    }
    if ((Get-ReleaseRepoUrl) -eq $recorded) { return @() }
    return @('--force-reinstall')
}

# HARDY_RELEASE_BASE_URL replaces the location wholesale, which is how the
# installer's own CI exercises this path against a release it built moments
# earlier, before one has ever been published. It is deliberately not recorded:
# it names a place for one run, where the repository names where this
# installation's code comes from for good.
function Get-ReleaseBaseUrl {
    if ($env:HARDY_RELEASE_BASE_URL) { return $env:HARDY_RELEASE_BASE_URL.TrimEnd('/') }
    $repository = Get-ReleaseRepoUrl
    if ($ReleaseVersion) { return "$repository/releases/download/$ReleaseVersion" }
    return "$repository/releases/latest/download"
}

# Find one asset in a SHA256SUMS manifest by the end of its name. The version is
# in the filename, so this is also how the installer learns which release it is
# about to install without being told.
function Find-ReleaseAsset($manifest, $suffix) {
    foreach ($line in [System.IO.File]::ReadAllLines($manifest)) {
        $fields = $line.Trim() -split '\s+', 2
        if ($fields.Count -ne 2) { continue }
        # sha256sum marks binary-mode entries with a leading asterisk.
        $name = $fields[1].Trim().TrimStart('*')
        if ($name.EndsWith($suffix)) { return @{ Digest = $fields[0]; Name = $name } }
    }
    return $null
}

# Download one asset and refuse it unless it matches the manifest. The wheel is
# code that will run as this user, so a mismatch stops the install — always,
# whatever $required says. Only a missing manifest (404), without an explicit
# release selector, is answered with $null to offer development-source fallback.
function Save-ReleaseAsset($suffix, $directory, $required = $true) {
    $base = Get-ReleaseBaseUrl
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $manifest = Join-Path $directory 'SHA256SUMS'
    # The manifest the hand-off already fetched, when there is one. It names the
    # versioned assets, so reusing it keeps one install run on one release even
    # if another is published while prerequisites are being installed.
    if ($env:HARDY_RELEASE_MANIFEST -and (Test-Path -LiteralPath $env:HARDY_RELEASE_MANIFEST)) {
        Copy-Item -LiteralPath $env:HARDY_RELEASE_MANIFEST -Destination $manifest -Force
    }
    else {
        try { Invoke-WebRequest -Uri "$base/SHA256SUMS" -OutFile $manifest -UseBasicParsing }
        catch {
            $status = $null
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if (-not $required -and $status -eq 404) { return $null }
            Stop-Install "could not fetch $base/SHA256SUMS: $($_.Exception.Message)"
        }
    }
    $asset = Find-ReleaseAsset $manifest $suffix
    if (-not $asset) {
        Stop-Install "the release at $base has no $suffix asset"
    }
    $path = Join-Path $directory $asset.Name
    try { Invoke-WebRequest -Uri "$base/$($asset.Name)" -OutFile $path -UseBasicParsing }
    catch {
        Stop-Install "could not download $base/$($asset.Name): $($_.Exception.Message)"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower()
    if ($actual -ne $asset.Digest.ToLower()) {
        Stop-Install "checksum mismatch for $($asset.Name): the release says $($asset.Digest), the download is $actual"
    }
    return $path
}

# The POSIX installers cannot run at all without scripts\lib beside them, so
# they fetch the release's own installer scripts and hand over to those. This
# file needs nothing beside it — and would therefore install a release using
# whatever logic the copy on disk happens to have, which is the version skew the
# bundle exists to prevent. So it hands over too. HARDY_INSTALLER_HANDED_OFF
# marks the copy that was fetched, which must not fetch again.
function Invoke-ReleaseInstaller {
    if ($env:HARDY_INSTALLER_HANDED_OFF) { return }
    # tar ships with Windows 10 1803 and Server 2019 and later. Installing the
    # wheel anyway would put a release on the machine with no updater or
    # uninstaller beside it, and on a reinstall would pair a new wheel with the
    # retained scripts of an older release — the skew this hand-off exists to
    # prevent. Refusing says which one thing is missing.
    if (-not (Test-Command 'tar')) {
        Stop-Install 'tar is required to unpack the release installers, and is not on this machine. It ships with Windows 10 1803 and Server 2019 and later; on an older one, install Hardy from a checkout instead (git clone, then scripts\install-windows.ps1).'
    }
    Write-Step "Fetching the Hardy installers from $(Get-ReleaseBaseUrl)"
    $installers = Join-Path $Prefix 'installers'
    $staging = Join-Path $Prefix 'installers.new'
    Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    # Required only when a particular release was asked for. Otherwise an
    # missing manifest (404) may mean there is no release yet. Transport errors
    # cannot establish that and never select development source.
    # -FromRelease and HARDY_INSTALL_FROM=release are as explicit as naming a
    # version: installing the development branch (unreleased source) would install the very thing the
    # caller ruled out.
    $named = [bool]($env:HARDY_VERSION -or $env:HARDY_RELEASE_BASE_URL -or
        $FromRelease -or ($env:HARDY_INSTALL_FROM -eq 'release'))
    $bundle = Save-ReleaseAsset 'hardy-installers.tar.gz' (Join-Path $staging 'download') $named
    if (-not $bundle) {
        Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
        Write-Warn "no release to install from at $(Get-ReleaseBaseUrl); installing the development branch (unreleased source)"
        if (-not (Confirm-Step 'Install the unreleased development branch instead?')) { Stop-Install 'source fallback declined' }
        $script:InstallFrom = 'source'
        return
    }
    Write-Detail "verified $(Split-Path -Leaf $bundle) against the release manifest"
    $tree = Join-Path $staging 'tree'
    New-Item -ItemType Directory -Force -Path $tree | Out-Null
    & tar -xzf $bundle -C $tree
    if ($LASTEXITCODE -ne 0) { Stop-Install "could not unpack $(Split-Path -Leaf $bundle)" }
    $handoff = Join-Path $tree 'scripts\install-windows.ps1'
    if (-not (Test-Path $handoff)) { Stop-Install 'the release installer bundle carries no install-windows.ps1' }
    # Left staged. The retained installers of an existing installation are not
    # replaced until the wheel they came with is installed, or a failure after
    # this would leave release N's wheel under release N+1's updater.
    # Complete-ReleaseInstallers does the swap once the wheel is in.
    $env:HARDY_RELEASE_MANIFEST = Join-Path $staging 'download\SHA256SUMS'

    # Everything this copy was asked for, passed to the one that will do it.
    $forward = @()
    foreach ($name in $ScriptParameters.Keys) {
        $value = $ScriptParameters[$name]
        if ($value -is [System.Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) { $forward += "-$name" }
        }
        else { $forward += @("-$name", [string]$value) }
    }
    $env:HARDY_INSTALLER_HANDED_OFF = '1'
    Write-Detail "handing over to $handoff"
    & powershell -ExecutionPolicy Bypass -File $handoff @forward
    exit $LASTEXITCODE
}

# Swapped whole, and only once the wheel is installed.
function Complete-ReleaseInstallers {
    $tree = Join-Path $Prefix 'installers.new\tree'
    if (-not (Test-Path $tree)) { return }
    $installers = Join-Path $Prefix 'installers'
    Remove-Item -Recurse -Force $installers -ErrorAction SilentlyContinue
    Move-Item $tree $installers
    Remove-Item -Recurse -Force (Join-Path $Prefix 'installers.new') -ErrorAction SilentlyContinue
    Write-Detail "the installers in $installers now match the installed release"
}

# A checkout is what a developer running this from one means. Anything else has
# no source to install and takes the release; naming HARDY_REPO_REF asks for the
# repository instead, which is how a fork or a branch is installed.
function Resolve-InstallSource {
    if ($FromRelease -and $FromSource) { Stop-Install '-FromRelease and -FromSource cannot both be given' }
    $checkout = [bool]($RepoRoot -and (Test-Path (Join-Path $RepoRoot 'pyproject.toml')))
    if ($FromRelease) { $script:InstallFrom = 'release'; return }
    if ($FromSource) {
        if (-not ($checkout -or $env:HARDY_REPO_REF)) {
            Stop-Install "-FromSource was asked for, but there is no Hardy source tree at $RepoRoot"
        }
        $script:InstallFrom = 'source'
        return
    }
    if ($env:HARDY_INSTALL_FROM) {
        if ($env:HARDY_INSTALL_FROM -notin @('release', 'source')) {
            Stop-Install "HARDY_INSTALL_FROM must be 'release' or 'source', not '$($env:HARDY_INSTALL_FROM)'"
        }
        $script:InstallFrom = $env:HARDY_INSTALL_FROM
        return
    }
    $script:InstallFrom = if ($checkout -or $env:HARDY_REPO_REF) { 'source' } else { 'release' }
}

# Only reached on the source path with no checkout here — HARDY_REPO_REF naming
# a fork or a branch, which has no release to download from.
function Initialize-Repository {
    if ($RepoRoot -and (Test-Path (Join-Path $RepoRoot 'pyproject.toml'))) { return $false }
    $url = $RepoUrl
    $reference = if ($env:HARDY_REPO_REF) { $env:HARDY_REPO_REF } else { 'main' }
    $source = Join-Path $Prefix 'src'
    # Always re-fetched, and into a sibling: reusing whatever is already there
    # would reinstall the previous ref after the selector changed, and deleting
    # it first would break an editable installation if the fetch then failed.
    $staging = "$source.new"
    Write-Step "Fetching Hardy into $source (ref $reference)"
    Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    if (Test-Command 'git') {
        $cloneOutput = Invoke-Native git @('clone', '--depth', '1', '--branch', $reference, $url, $staging) -CaptureErrors
        foreach ($line in $cloneOutput) { Write-Detail ([string]$line) }
        if ($LASTEXITCODE -ne 0) { Stop-Install "git clone of $url (ref $reference) failed" }
    }
    else {
        $download = Join-Path ([System.IO.Path]::GetTempPath()) ("hardy-src-" + [System.Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force -Path $download | Out-Null
        try {
            $archive = Join-Path $download 'hardy.zip'
            # A ref may be a branch or a tag, and GitHub keeps the two in
            # separate namespaces; a machine with no git cannot ask which.
            $fetched = $false
            foreach ($namespace in @('heads', 'tags')) {
                try {
                    Invoke-WebRequest -Uri "$url/archive/refs/$namespace/$reference.zip" -OutFile $archive -UseBasicParsing
                    $fetched = $true
                    break
                }
                catch { continue }
            }
            if (-not $fetched) { Stop-Install "could not download $url (ref $reference)" }
            Expand-Archive -Path $archive -DestinationPath $download -Force
            # GitHub archives wrap everything in a <repo>-<ref> directory.
            $extracted = Get-ChildItem -Directory $download | Select-Object -First 1
            if (-not $extracted) { Stop-Install "the downloaded archive from $url was empty" }
            Copy-Item -Path (Join-Path $extracted.FullName '*') -Destination $staging -Recurse -Force
        }
        finally { Remove-Item -Recurse -Force $download -ErrorAction SilentlyContinue }
    }
    if (-not (Test-Path (Join-Path $staging 'pyproject.toml'))) {
        Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
        Stop-Install "what was fetched from $url (ref $reference) is not the Hardy repository"
    }
    Remove-Item -Recurse -Force $source -ErrorAction SilentlyContinue
    Move-Item $staging $source
    $script:RepoRoot = $source
    return $true
}

# Sets $script:Python rather than returning it: every command a PowerShell
# function runs contributes to its return value, and winget is chatty.
function Install-Prerequisites {
    Write-Step 'Checking for Python 3.11 or newer, and Git'
    $script:Python = Get-Python
    if (-not $script:Python) {
        if (-not (Confirm-Step 'Install Python 3.12 with winget?')) { Stop-Install 'Python 3.11+ is required' }
        Install-WithWinget 'Python.Python.3.12' 'Python'
        $script:Python = Get-Python
        if (-not $script:Python) { Stop-Install 'Python was installed but is not on PATH; open a new PowerShell window and re-run' }
    }
    Write-Detail "using $script:Python ($(& $script:Python --version))"
    if (-not (Test-Command 'git')) {
        if (-not (Confirm-Step 'Install Git with winget? (lake needs it to fetch Mathlib)')) { Stop-Install 'git is required' }
        Install-WithWinget 'Git.Git' 'Git'
    }
}

function New-Environment {
    Write-Step "Installing Hardy into $Venv"
    $python = $script:Python
    New-Item -ItemType Directory -Force -Path $Prefix | Out-Null
    & $python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Stop-Install 'could not create the virtual environment' }
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    & $venvPython -m pip install --upgrade pip
    if ($script:InstallFrom -eq 'release') {
        # The download lands under the prefix rather than in a temporary
        # directory: a wheel that failed verification is worth being able to
        # look at, and the next run replaces it either way.
        $directory = Join-Path $Prefix 'download'
        Remove-Item -Recurse -Force $directory -ErrorAction SilentlyContinue
        $wheel = Save-ReleaseAsset '.whl' $directory
        Write-Detail "verified $(Split-Path -Leaf $wheel) against the release manifest"
        $arguments = @('-m', 'pip', 'install') + (Get-ReinstallArguments)
        & $venvPython @arguments $wheel
        if ($LASTEXITCODE -ne 0) { Stop-Install "could not install $(Split-Path -Leaf $wheel) into $Venv" }
        Write-Detail "installed hardy from $(Split-Path -Leaf $wheel)"
        Remove-Item -Recurse -Force $directory -ErrorAction SilentlyContinue
        Save-ReleaseOrigin
        Complete-ReleaseInstallers
    }
    else {
        & $venvPython -m pip install -e $RepoRoot
        if ($LASTEXITCODE -ne 0) { Stop-Install 'pip install failed' }
        Write-Detail "installed hardy (editable, from $RepoRoot)"
    }
    if (-not (Test-Path (Join-Path $Venv 'Scripts\hardy.exe'))) { Stop-Install "the hardy command was not installed into $Venv" }
}

# Two paths are the same directory once relative segments, casing, and a
# trailing separator are normalised away -- the comparison `Add-Shim` needs to
# tell the default layout (`<Prefix>\bin` beside `<Prefix>\venv`) from a
# `-BinDir` pointed somewhere else entirely.
function Test-SamePath($a, $b) {
    ([System.IO.Path]::GetFullPath($a)).TrimEnd('\') -ieq ([System.IO.Path]::GetFullPath($b)).TrimEnd('\')
}

# Pure: the "..\.." path from $BinDir to $Target, PowerShell 5.1 has no
# [IO.Path]::GetRelativePath. Strips the common prefix of the two full,
# normalised paths (case-insensitively) and emits one `..` per remaining
# $BinDir segment, then $Target's own remaining segments. $null when the two
# do not share a root at all (different drives, or a drive versus a UNC
# share) -- there is no relative path across that boundary.
function Get-RelativeShimPath($BinDir, $Target) {
    $binFull = [System.IO.Path]::GetFullPath($BinDir)
    $targetFull = [System.IO.Path]::GetFullPath($Target)
    $binRoot = [System.IO.Path]::GetPathRoot($binFull)
    $targetRoot = [System.IO.Path]::GetPathRoot($targetFull)
    if (-not $binRoot -or -not $targetRoot -or $binRoot -ine $targetRoot) { return $null }
    # The root is trimmed off, not the whole path, before TrimEnd: $BinDir
    # given as the root itself (say "C:\") is otherwise one character shorter
    # than its own root once trailing-backslash-trimmed, and Substring throws
    # rather than returning empty.
    $binParts = @($binFull.Substring($binRoot.Length).TrimEnd('\') -split '\\' | Where-Object { $_ })
    $targetParts = @($targetFull.Substring($targetRoot.Length) -split '\\' | Where-Object { $_ })
    $common = 0
    while ($common -lt $binParts.Count -and $common -lt $targetParts.Count -and
        $binParts[$common] -ieq $targetParts[$common]) {
        $common++
    }
    $upCount = $binParts.Count - $common
    # A PowerShell range where the end is before the start counts DOWN
    # instead of returning empty ($targetParts[3..2] is @(3, 2), not @()), so
    # the no-segments-left case is guarded rather than left to the range.
    #
    # The leading `,` on each branch matters: `$x = if (...) { @(...) }`
    # still unwraps the branch's array through the if-expression's own
    # pipeline capture -- a one-element array becomes a bare scalar, and an
    # empty one becomes $null, not @(). `, @(...)` emits the array as a
    # single pipeline object instead, so the if-expression captures the
    # array itself either way. Without this, `@('..') * $upCount + $null`
    # (from the empty case) appends a stray $null element rather than
    # nothing, and `$segments.Count -eq 0` could never be true for the
    # same-directory case below.
    $downParts = if ($common -lt $targetParts.Count) { , @($targetParts[$common..($targetParts.Count - 1)]) } else { , @() }
    $segments = @('..') * $upCount + $downParts
    if ($segments.Count -eq 0) { return '.' }
    return ($segments -join '\')
}

# Pure: computes the text and encoding for hardy.cmd without touching PATH or
# disk, so it can be exercised directly. cmd.exe decodes a .cmd file using the
# console's *current* code page (chcp / GetConsoleCP), not a fixed system
# value, so there is no code page this function could pick that is reliably
# right -- a byte written to be correct for one console reads back wrong in
# another. The only path that is safe under every code page is one made of
# nothing but ASCII bytes, so hardy.cmd is always written as pure ASCII;
# `chcp 65001` is not used to work around this, since it would change the
# whole console's encoding for whatever the user runs next in it. (#305)
#
# In order:
#  1. The default layout (`$BinDir` beside `$Venv`'s parent): the shim finds
#     the venv relative to itself via `%~dp0` and never encodes a path at all.
#  2. A custom `-BinDir` on the same drive as the venv, whose relative path to
#     it is itself pure ASCII: also `%~dp0`-relative, just computed rather
#     than the fixed literal above.
#  3. A custom `-BinDir` elsewhere, but the venv sits under %LOCALAPPDATA% or
#     %USERPROFILE% and the remainder past that is pure ASCII: written
#     relative to that variable, which cmd.exe expands to the real value at
#     run time, whatever is in it.
#  4. The target's own 8.3 short path, if the volume still generates one (many
#     images no longer do) and it happens to be pure ASCII -- 8.3 names are
#     restricted to a small character set that normally never needs this
#     check, but nothing here assumes that without verifying it.
#  5. Otherwise, refuse rather than write a path that would read back wrong.
# `%` is escaped as `%%` wherever a path is interpolated into the batch line,
# since cmd.exe treats an unescaped `%x` as a batch variable reference. Every
# ASCII round-trip check below uses `-cne` (case-sensitive): the default
# `-ne` in PowerShell ignores case, so it would call an encoding correct even
# where it substituted, say, an accented capital for its lowercase form.
function Get-ShimContent($BinDir, $Venv) {
    $target = Join-Path $Venv 'Scripts\hardy.exe'
    $prefix = Split-Path -Parent $Venv
    $ascii = New-Object System.Text.ASCIIEncoding

    # %~dp0 resolves correctly for the cases hardy.cmd is actually reached
    # by: an unquoted bare name found via PATH search (`hardy --help`, the
    # ordinary case), or an explicit relative, absolute, or UNC path. The one
    # real, documented pitfall is a *quoted* bare name still resolved via
    # PATH search (`"hardy" --help`) -- cmd.exe then reports %~dp0 as the
    # caller's own current directory, not this file's. The usual fix is a
    # `call`-to-a-label indirection that captures %~dp0 inside the called
    # label before %* is ever touched. It is deliberately not used here:
    # neither that indirection nor its own use of %~dp0 can be exercised
    # without a real Windows machine to confirm it against, and a mistake in
    # it would break the default, by far most common, install path for
    # everyone -- not just the narrow quoted-bare-name case it would fix.
    # Nothing this installer controls ever invokes hardy.cmd that way:
    # Test-Installation runs it by a fully quoted absolute path, which
    # %~dp0 always resolves correctly for regardless of quoting. The gap is
    # left open rather than risking the common path to close an edge one.
    if (Test-SamePath (Split-Path -Parent $BinDir) $prefix) {
        return [pscustomobject]@{
            Text     = "@echo off`r`n`"%~dp0..\venv\Scripts\hardy.exe`" %*`r`n"
            Encoding = $ascii
        }
    }

    $relative = Get-RelativeShimPath $BinDir $target
    if ($null -ne $relative) {
        $escapedRelative = $relative -replace '%', '%%'
        if ($ascii.GetString($ascii.GetBytes($escapedRelative)) -ceq $escapedRelative) {
            return [pscustomobject]@{
                Text     = "@echo off`r`n`"%~dp0$escapedRelative`" %*`r`n"
                Encoding = $ascii
            }
        }
    }

    foreach ($variable in @('LOCALAPPDATA', 'USERPROFILE')) {
        $root = ([Environment]::GetEnvironmentVariable($variable))
        if (-not $root) { continue }
        $root = $root.TrimEnd('\')
        if (-not $target.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        # A boundary check, not just a prefix one: "...\AppData\Local" must
        # not match a target under "...\AppData\LocalLow\..." merely because
        # the text happens to start the same way.
        if ($target.Length -ne $root.Length -and $target[$root.Length] -ne '\') { continue }
        $relative = ($target.Substring($root.Length).TrimStart('\')) -replace '%', '%%'
        if ($ascii.GetString($ascii.GetBytes($relative)) -cne $relative) { continue }
        return [pscustomobject]@{
            Text     = "@echo off`r`n`"%$variable%\$relative`" %*`r`n"
            Encoding = $ascii
        }
    }

    $shortPath = $null
    try {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        $shortPath = $fso.GetFile($target).ShortPath
    }
    catch {
        # No 8.3 name to have: the file is missing, or (increasingly common)
        # the volume has short-name generation turned off. Either way, there
        # is nothing usable here, not an error to report.
        $shortPath = $null
    }
    if ($shortPath) {
        $escapedShort = $shortPath -replace '%', '%%'
        if ($ascii.GetString($ascii.GetBytes($escapedShort)) -ceq $escapedShort) {
            return [pscustomobject]@{
                Text     = "@echo off`r`n`"$escapedShort`" %*`r`n"
                Encoding = $ascii
            }
        }
    }

    throw "$target cannot be written into a .cmd file reliably; install with -BinDir inside $prefix"
}

function Add-Shim {
    Write-Step "Linking the hardy command into $BinDir"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    try {
        $shim = Get-ShimContent $BinDir $Venv
    }
    catch {
        Stop-Install $_.Exception.Message
    }
    [System.IO.File]::WriteAllText((Join-Path $BinDir 'hardy.cmd'), $shim.Text, $shim.Encoding)
    $userPath = Get-UserPathRaw
    if (-not (Test-PathEntry $userPath.Value $BinDir)) {
        Set-UserPathRaw "$BinDir;$($userPath.Value)" $userPath.Kind
        Write-Detail "added $BinDir to your user PATH (new terminals only)"
    }
    Update-SessionPath
}

function New-Launcher($Target, $Directory, $WorkingDirectory, $IconPath) {
    # A .lnk through the Shell's own COM object: the one way to make a
    # shortcut Windows treats as a shortcut (pinnable, with a working
    # directory) without a compiled helper. Nothing is made when the folder
    # does not exist -- a machine with a redirected or removed Desktop is not
    # an error, it has nowhere to put one.
    if (-not (Test-Path -LiteralPath $Directory)) { return $null }
    $path = Join-Path $Directory 'Hardy.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $Target
    $link.Arguments = 'web --open'
    $link.WorkingDirectory = $WorkingDirectory
    $link.Description = 'Hardy: serve the browser client and open it'
    if ($IconPath -and (Test-Path -LiteralPath $IconPath)) {
        $link.IconLocation = "$IconPath,0"
    }
    $link.Save()
    return $path
}

function Add-Launcher {
    if ($NoLauncher) { return }
    Write-Step 'Adding the Hardy launcher to the Desktop and the Start Menu'
    # The venv's own hardy.exe, the same target hardy.cmd wraps: stable
    # across updates and the same for a release and an editable install.
    $target = Join-Path $Venv 'Scripts\hardy.exe'
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    $icon = & $venvPython -c "from pathlib import Path; import hardy; print(Path(hardy.__file__).parent / 'app' / 'web' / 'static' / 'hardy.ico')"
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        $made = New-Launcher $target $folder $Prefix $icon
        if ($made) { Write-Detail "wrote $made" }
    }
    Write-Detail 'the launcher runs `hardy web --open` in a console window; Ctrl+C there stops the server'
}

function Install-Elan {
    Write-Step 'Checking for the Lean toolchain (lake)'
    Update-SessionPath
    if (Test-Command 'lake') {
        Write-Detail "lake present: $(& lake --version)"
        return
    }
    if (-not (Confirm-Step 'Install elan (the Lean toolchain manager, which provides lake)?')) {
        Stop-Install 'lake is required; re-run with -SkipMathlib only if you will install Lean yourself'
    }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { Stop-Install 'The pinned elan release has no Windows ARM64 bootstrap; install elan manually, then re-run.' }
    $architecture = 'x86_64'
    $asset = "elan-$architecture-pc-windows-msvc.zip"
    $url = "https://github.com/leanprover/elan/releases/download/v4.2.4/$asset"
    $digest = 'fad2e980a191c15884cc1d80d170ffc5fa84f3774541020145b66d1a644c6111'
    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hardy-elan-" + [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        Write-Detail "downloading $url"
        $archive = Join-Path $staging $asset
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLower() -ne $digest) { Stop-Install 'elan bootstrap checksum mismatch; nothing was executed' }
        Expand-Archive -Path $archive -DestinationPath $staging -Force
        $initializer = Get-ChildItem -Path $staging -Filter 'elan-init*.exe' -Recurse | Select-Object -First 1
        if (-not $initializer) { Stop-Install "elan-init.exe was not found inside $asset" }
        & $initializer.FullName -y --default-toolchain stable
        if ($LASTEXITCODE -ne 0) { Stop-Install "elan-init failed (exit $LASTEXITCODE)" }
    }
    finally {
        Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    }
    Update-SessionPath
    if (-not (Test-Command 'lake')) { Stop-Install 'elan was installed but lake is not on PATH; open a new PowerShell window and re-run' }
    Write-Detail "installed $(& elan --version)"
}

function Test-LeanProject {
    $probe = Join-Path ([System.IO.Path]::GetTempPath()) ("hardy-probe-" + [System.Guid]::NewGuid().ToString('N') + '.lean')
    Write-Utf8File $probe "import Mathlib`n`nexample : 2 + 2 = 4 := by norm_num`n"
    try {
        Push-Location $LeanProject
        # A PowerShell function's return value is everything its body writes
        # to the output stream, from any statement, not just the one after
        # `return` -- so a bare `& lake --version` here would make this
        # function return @('Lake version ...', $true_or_false), a
        # two-element array that is always truthy regardless of which. Piped
        # to Out-Host instead: still visible, still under Invoke-Native's
        # Continue (elan's toolchain download is what actually happens on a
        # first-ever `lake` invocation and needs to show its progress; the
        # Mathlib clone itself happens inside the probe below, silenced), but
        # consumed there rather than added to what this function returns.
        Invoke-Native lake @('--version') | Out-Host
        Invoke-Native lake @('env', 'lean', $probe) -Quiet
        return ($LASTEXITCODE -eq 0)
    }
    finally {
        Pop-Location
        Remove-Item -Force $probe -ErrorAction SilentlyContinue
    }
}

function Install-LeanProject {
    if ($SkipMathlib) {
        Write-Step 'Skipping the Mathlib project (-SkipMathlib)'
        Write-Warn "run hardy from your own Lake project, or set lean_project in $ConfigPath"
        return
    }
    Write-Step "Preparing the shared Mathlib project at $LeanProject"
    $lakefile = @('lakefile.toml', 'lakefile.lean') | ForEach-Object { Join-Path $LeanProject $_ } | Where-Object { Test-Path $_ }
    if (-not $lakefile) {
        New-Item -ItemType Directory -Force -Path $LeanProject | Out-Null
        if ((Get-ChildItem -Force $LeanProject | Measure-Object).Count -gt 0) {
            Stop-Install "$LeanProject exists and is not a Lake project; move it aside"
        }
        Write-Detail "creating a Lake project pinned to $LeanToolchain and Mathlib $MathlibRevision"
        # Written rather than generated by `lake init`: the `math` template
        # requires Mathlib at whatever its default branch holds, and a project
        # that moves when upstream does cannot be the environment a recorded
        # result names.
        Write-Utf8File (Join-Path $LeanProject 'lean-toolchain') "$LeanToolchain`n"
        Write-Utf8File (Join-Path $LeanProject 'lakefile.toml') @"
name = "$LeanPackage"
defaultTargets = ["HardyMath"]

[[require]]
name = "mathlib"
scope = "leanprover-community"
rev = "$MathlibRevision"

[[lean_lib]]
name = "HardyMath"
"@
        Write-Utf8File (Join-Path $LeanProject 'HardyMath.lean') "import Mathlib`n"
    }
    else {
        Write-Detail 'reusing the existing project'
    }
    if (Test-LeanProject) {
        Write-Detail 'Mathlib already builds here; nothing to download'
        return
    }
    Write-Detail 'fetching Mathlib and its prebuilt cache - several gigabytes, typically 10-30 minutes'
    Push-Location $LeanProject
    try {
        & lake update
        if ($LASTEXITCODE -ne 0) { Stop-Install 'lake update failed' }
        & lake exe cache get
        if ($LASTEXITCODE -ne 0) { Stop-Install 'lake exe cache get failed' }
        & lake build
        if ($LASTEXITCODE -ne 0) { Stop-Install 'lake build failed' }
    }
    finally { Pop-Location }
    if (-not (Test-LeanProject)) { Stop-Install "the Lean project was built but 'import Mathlib' still fails in $LeanProject" }
    Write-Detail 'Mathlib is ready'
}

function Install-Latex {
    if ($SkipLatex) {
        Write-Step 'Skipping LaTeX (-SkipLatex)'
        return
    }
    if (-not $FullLatex) {
        Write-Step 'Using pinned Tectonic for LaTeX; provisioned after configuration'
        return
    }
    Write-Step 'Checking for pdflatex'
    Update-SessionPath
    if (Test-Command 'pdflatex') {
        Write-Detail "pdflatex present: $((& pdflatex --version | Select-Object -First 1))"
        return
    }
    if (-not (Confirm-Step 'Install a TeX distribution providing pdflatex?')) {
        Write-Warn 'continuing without pdflatex; Hardy writeup tools will fail'
        return
    }
    Install-WithWinget 'TeXLive.TeXLive' 'TeX Live'
    Update-SessionPath
    if (-not (Test-Command 'pdflatex')) {
        Stop-Install 'pdflatex is still not on PATH after installing TeX; open a new PowerShell window and re-run'
    }
    Write-Detail "installed $((& pdflatex --version | Select-Object -First 1))"
}

function ConvertTo-TomlString($value) { $value -replace '\\', '\\\\' -replace '"', '\"' }

# Before the file below is created, and that order is the whole point. Hardy's
# own `migrate_global` moves a pre-`~/.hardy` config into place and DECLINES
# when the destination already exists -- so an installer that wrote the new file
# first left an upgrading user's model, commands and timeouts stranded in the
# legacy file (`%APPDATA%\hardy\config.toml`) forever, with nothing left to
# trigger the move. Run through the installed Hardy rather than reimplemented
# here: only it knows where the legacy file lives and which settings survive.
function Move-LegacyConfig {
    if (Test-Path $ConfigPath) { return }
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $venvPython)) { return }
    $program = 'import sys; from pathlib import Path; from hardy.app.config import migrate_global; sys.exit(0 if migrate_global(destination=Path(sys.argv[1])) else 1)'
    Invoke-Native $venvPython @('-c', $program, $ConfigPath) -DropErrors
    if ($LASTEXITCODE -eq 0) {
        Write-Detail "moved your settings from the older config location into $ConfigPath"
    }
}

function Write-Config {
    if ($NoConfig) {
        Write-Step 'Skipping the config file (-NoConfig)'
        return
    }
    Write-Step "Writing $ConfigPath"
    Move-LegacyConfig
    if (Test-Path $ConfigPath) {
        Write-Detail 'config already exists; leaving your model and key untouched'
        if (-not $SkipMathlib -and -not (Select-String -Path $ConfigPath -Pattern '^\s*lean_project' -Quiet)) {
            Add-Utf8Line $ConfigPath ('lean_project = "{0}"' -f (ConvertTo-TomlString $LeanProject))
            Write-Detail "recorded lean_project = $LeanProject"
        }
        return
    }
    $model = if ($env:HARDY_MODEL) { $env:HARDY_MODEL } else { '' }
    if (-not $model -and -not $Yes -and [Environment]::UserInteractive) {
        Write-Host "`nHardy talks to Claude through your Claude Code subscription."
        Write-Host 'There is no API key to supply; sign in once with `claude login`.'
        $model = Read-Host 'Model identity [claude-opus-5]'
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ConfigPath) | Out-Null
    $lines = @(
        '# Written by the Hardy installer. Every value can be overridden by a',
        '# HARDY_* environment variable or a command-line flag.'
    )
    # Only settings the parser accepts: anything else makes every later Hardy
    # invocation fail with "unknown settings".
    if ($model) { $lines += 'model = "{0}"' -f (ConvertTo-TomlString $model) }
    if (-not $SkipMathlib) { $lines += 'lean_project = "{0}"' -f (ConvertTo-TomlString $LeanProject) }
    Write-Utf8File $ConfigPath (($lines -join "`r`n") + "`r`n")
    $script:ConfiguredModel = $model
    Write-Detail "wrote $ConfigPath"
}

function Install-ClaudeCli {
    Write-Step 'Checking the Claude Code CLI'
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        Write-Detail 'claude already installed'
    }
    else {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            if (-not (Confirm-Step 'Install Node.js LTS (shared tool) to provide npm for the Claude Code CLI?')) {
                Write-Warn 'Node.js installation declined; install the Claude Code CLI before using Hardy'
                $script:VerificationFailed = $true
                return
            }
            if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
                Write-Warn 'winget is unavailable; install Node.js LTS and the Claude Code CLI, then rerun'
                $script:VerificationFailed = $true
                return
            }
            Write-Detail 'installing Node.js LTS with winget'
            Invoke-Native winget @('install', '--id', 'OpenJS.NodeJS.LTS', '--exact', '--source', 'winget',
                '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity')
            if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
                Write-Warn "winget could not install Node.js LTS (exit $LASTEXITCODE)"
                $script:VerificationFailed = $true
                return
            }
            Update-SessionPath
            if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
                Write-Warn 'npm is still unavailable after Node.js installation; open a new terminal and rerun'
                $script:VerificationFailed = $true
                return
            }
        }
        Write-Detail 'installing @anthropic-ai/claude-code'
        Invoke-Native npm @('install', '-g', '@anthropic-ai/claude-code') -Quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "npm could not install @anthropic-ai/claude-code (exit $LASTEXITCODE)"
            $script:VerificationFailed = $true
            return
        }
        Update-SessionPath
        if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
            Write-Warn 'the Claude Code CLI is still unavailable; open a new terminal and rerun'
            $script:VerificationFailed = $true
            return
        }
    }
    $status = (Invoke-Native claude @('auth', 'status') -DropErrors) -join ''
    if ($status -notmatch '"loggedIn"\s*:\s*true') { Write-Detail "run 'claude login' to sign in with your subscription" }
}

function Install-Tectonic {
    if ($SkipLatex) { return }
    Write-Step 'Installing pinned, digest-checked Tectonic'
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    $program = @'
import sys
from pathlib import Path
from hardy.app.installers import install_tectonic, download_file
result = install_tectonic(destination_root=Path(sys.argv[1]), confirmer=lambda _: True, downloader=download_file)
if not result.installed_path:
    print(result.manual_instructions)
    raise SystemExit(1)
print(result.installed_path)
'@
    $installed = Invoke-Native $venvPython @('-', (Join-Path $Prefix 'tools')) -InputObject $program
    if ($LASTEXITCODE -ne 0 -or -not $installed) { Stop-Install 'Tectonic could not be installed' }
    $script:InstalledTectonic = ([string]($installed | Select-Object -Last 1)).Trim()
    # This also supports -NoConfig: the current verification uses the explicit
    # executable, and the summary tells the user how to persist the setting.
    $hasTectonicSetting = (Test-Path -LiteralPath $ConfigPath) -and (Select-String -LiteralPath $ConfigPath -Pattern '^\s*tectonic\s*=' -Quiet)
    if (-not $env:HARDY_TECTONIC -and -not $hasTectonicSetting) { $env:HARDY_TECTONIC = $script:InstalledTectonic }
    if (-not $NoConfig -and -not $hasTectonicSetting) {
        Add-Utf8Line $ConfigPath ('tectonic = "{0}"' -f (ConvertTo-TomlString $script:InstalledTectonic))
    }
    # Tectonic is also the default compiler for interactive cells on Windows.
    # Keep .log/.aux artifacts consumed by LatexTools; preserve explicit choices.
    $hasLatexSetting = (Test-Path -LiteralPath $ConfigPath) -and (Select-String -LiteralPath $ConfigPath -Pattern '^\s*latex_command\s*=' -Quiet)
    if (-not $FullLatex -and -not $hasLatexSetting -and -not $env:HARDY_LATEX_COMMAND) {
        $compiler = ConvertTo-TomlString $script:InstalledTectonic
        if (-not $NoConfig) {
            Add-Utf8Line $ConfigPath ('latex_command = ["{0}", "--keep-logs", "--keep-intermediates"]' -f $compiler)
        }
        else {
            $env:HARDY_LATEX_COMMAND = '"{0}" --keep-logs --keep-intermediates' -f $script:InstalledTectonic
            Write-Detail ('To persist the interactive compiler, add latex_command = ["' + $compiler + '", "--keep-logs", "--keep-intermediates"] to your config')
        }
    }
}

function Test-Installation {
    Write-Step 'Verifying the installation'
    $hardy = Join-Path $Venv 'Scripts\hardy.exe'
    $shim = Join-Path $BinDir 'hardy.cmd'
    # Checked before anything else, and reported on its own: a failure past
    # this point is specific to the shim, not to Hardy itself, only because
    # this already established that hardy.exe on its own works.
    & $hardy --help | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Install "$hardy did not run; the installation itself is broken. Re-run the installer." }
    $env:HARDY_CONFIG = $ConfigPath
    $hasModel = $ConfiguredModel -or $env:HARDY_MODEL -or
        ((Test-Path $ConfigPath) -and (Select-String -Path $ConfigPath -Pattern '^\s*model' -Quiet))
    # Authentication can only be completed by the user. Classify the real
    # doctor checks rather than making its aggregate exit status binding.
    $venvPython = Join-Path $Venv 'Scripts\python.exe'
    $program = @'
import sys
from hardy.app.config import load
from hardy.app.doctor import run_checks
skipped = set(sys.argv[1:]) | {"claude login"}
checks = run_checks(load())
for check in checks:
    print(check.line())
raise SystemExit(1 if any(c.required and not c.ok and c.name not in skipped for c in checks) else 0)
'@
    $advisory = @()
    if (-not $hasModel) { $advisory += 'model' }
    if ($SkipLatex) { $advisory += @('latex', 'tectonic') }
    if ($SkipMathlib) { $advisory += @('lean', 'mathlib') }
    Invoke-Native $venvPython (@('-') + $advisory) -InputObject $program
    if ($LASTEXITCODE -ne 0) {
        $script:VerificationFailed = $true
        Write-Warn 'hardy doctor reported failures in installer responsibilities (see above)'
    }
    Write-Detail "run 'claude login' to complete subscription sign-in"
    # hardy.exe is already known-good (the --help check above), so a failure
    # here implicates the shim specifically -- the command the summary tells
    # the user to run, and where #305 actually broke (Test-Installation used
    # to run hardy.exe directly and never noticed a broken hardy.cmd).
    cmd /c "`"$shim`" --help" | Out-Null
    if ($LASTEXITCODE -ne 0) { Stop-Install "$shim did not run; re-run the installer" }
}

function Write-Summary {
    if ($script:VerificationFailed) { Write-Warn 'Hardy installation needs attention; verification failed.' }
    else { Write-Host "`nHardy is installed." -ForegroundColor Green }
    Write-Host @"

  command      $(Join-Path $BinDir 'hardy.cmd')
  environment  $Venv
  installed    $(if ($script:InstallFrom -eq 'source') { "editable, from $RepoRoot" } else { 'from the published release' })
  lean project $LeanProject$(if ($SkipMathlib) { ' (skipped)' })
  config       $ConfigPath
  tectonic     $(if ($SkipLatex) { 'skipped (-SkipLatex); run hardy setup' } else { $script:InstalledTectonic })
  launcher     $(if ($NoLauncher) { 'none (-NoLauncher)' } else { 'Hardy.lnk on the Desktop and in the Start Menu (hardy web --open)' })

Start doing mathematics with an agent:

  hardy

Other useful commands:

  claude login           complete subscription sign-in
  hardy setup            finish configuration and smoke tests (including Tectonic)
  hardy doctor --deep     check Lean, Mathlib, LaTeX, and the model end to end
  hardy chat --root . --project my-project

Open a new terminal first, so that $BinDir is on your PATH.
"@
}

Write-Step "Installing Hardy on Windows ($([Environment]::OSVersion.Version))"
Resolve-InstallSource
# The hand-off may find there is no release to install from and choose the
# repository instead, so what it decided is read after it has run, not before.
if ($script:InstallFrom -eq 'release') { Invoke-ReleaseInstaller }
if ($script:InstallFrom -eq 'source') {
    Initialize-Repository | Out-Null
    Write-Detail "source tree: $RepoRoot"
}
else {
    Write-Detail "release: $(Get-ReleaseBaseUrl)"
}
Install-Prerequisites
New-Environment
Add-Shim
Add-Launcher
Install-Elan
Install-LeanProject
Install-Latex
Write-Config
Install-Tectonic
$script:VerificationFailed = $false
Install-ClaudeCli
Test-Installation
Write-Summary
if ($script:VerificationFailed) { exit 1 }
