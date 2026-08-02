<#
.SYNOPSIS
    One-shot Hardy install for Windows. No WSL required.

.DESCRIPTION
    Takes a clean Windows machine to a working `hardy` command: Python, the Lean
    toolchain (elan/lake), a Mathlib project, pdflatex, and Hardy itself. Run it
    from PowerShell, in a clone or on its own:

        powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

    No clone is required. Run from a checkout it installs that tree, editable;
    run on its own it downloads Hardy's published wheel, checks it against the
    release manifest, and installs that. Winget installs Python, Git, and
    MiKTeX; elan comes from its official release, exactly as on Linux and macOS.

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
    Install full TeX Live instead of MiKTeX's install-on-demand distribution.

.PARAMETER NoConfig
    Do not write the config file.

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
    [switch]$FromRelease,
    [switch]$FromSource,
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'hardy'),
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA 'hardy\bin')
)

$ErrorActionPreference = 'Stop'
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
$MathlibToolchain = 'leanprover-community/mathlib4:lean-toolchain'
$ConfigPath = if ($env:HARDY_CONFIG) { $env:HARDY_CONFIG } else { Join-Path $env:APPDATA 'hardy\config.toml' }
$ConfiguredModel = ''
$Python = ''
$RepoUrl = if ($env:HARDY_REPO_URL) { $env:HARDY_REPO_URL } else { 'https://github.com/charlesmsiegel/hardy' }
# Which release to install: a tag, or empty for whatever is current.
$ReleaseVersion = if ($env:HARDY_VERSION) { $env:HARDY_VERSION } else { '' }
# 'release' downloads the published wheel and needs no source tree at all;
# 'source' installs the tree this script came from, editable. Resolve-InstallSource
# decides between them from what is actually here.
$InstallFrom = ''

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Detail($message) { Write-Host "    $message" }
function Write-Warn($message) { Write-Warning $message }
function Stop-Install($message) { Write-Host "error: $message" -ForegroundColor Red; exit 1 }
function Test-Command($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

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
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
    foreach ($extra in @((Join-Path $env:USERPROFILE '.elan\bin'), $BinDir)) {
        if ((Test-Path $extra) -and ($env:Path -notlike "*$extra*")) { $env:Path = "$extra;$env:Path" }
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
        & $command.Source -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' *> $null
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
# whatever $required says. $required false means only that an unreachable
# release is answered with $null instead of an exit, so the caller can fall back
# to the repository when no particular release was asked for.
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
            if (-not $required) { return $null }
            Stop-Install "could not fetch $base/SHA256SUMS - is there a published release yet? (HARDY_VERSION selects one, -FromSource installs a checkout instead)"
        }
    }
    $asset = Find-ReleaseAsset $manifest $suffix
    if (-not $asset) {
        if (-not $required) { return $null }
        Stop-Install "the release at $base has no $suffix asset"
    }
    $path = Join-Path $directory $asset.Name
    try { Invoke-WebRequest -Uri "$base/$($asset.Name)" -OutFile $path -UseBasicParsing }
    catch {
        if (-not $required) { return $null }
        Stop-Install "could not download $base/$($asset.Name)"
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
    # tar ships with Windows 10 1803 and later; without it, this copy installs
    # the release itself rather than refusing to install at all.
    if (-not (Test-Command 'tar')) {
        Write-Warn 'tar is not available, so this script cannot hand over to the release installers; continuing with its own'
        return
    }
    Write-Step "Fetching the Hardy installers from $(Get-ReleaseBaseUrl)"
    $installers = Join-Path $Prefix 'installers'
    $staging = Join-Path $Prefix 'installers.new'
    Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    # Required only when a particular release was asked for. Otherwise an
    # unreachable release means there is not one yet, which is the state before
    # the first is published, and the repository is where Hardy comes from --
    # the same fallback all three POSIX bootstraps take.
    $named = [bool]($env:HARDY_VERSION -or $env:HARDY_RELEASE_BASE_URL)
    $bundle = Save-ReleaseAsset 'hardy-installers.tar.gz' (Join-Path $staging 'download') $named
    if (-not $bundle) {
        Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
        Write-Warn "no release to install from at $(Get-ReleaseBaseUrl); falling back to the repository"
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
    Copy-Item -LiteralPath (Join-Path $staging 'download\SHA256SUMS') -Destination (Join-Path $tree 'SHA256SUMS') -Force
    Remove-Item -Recurse -Force $installers -ErrorAction SilentlyContinue
    Move-Item $tree $installers
    Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
    $env:HARDY_RELEASE_MANIFEST = Join-Path $installers 'SHA256SUMS'

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
    Write-Detail "handing over to $(Join-Path $installers 'scripts\install-windows.ps1')"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $installers 'scripts\install-windows.ps1') @forward
    exit $LASTEXITCODE
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
    if (-not (Test-Path (Join-Path $source 'pyproject.toml'))) {
        Write-Step "Fetching Hardy into $source"
        Remove-Item -Recurse -Force $source -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $source | Out-Null
        if (Test-Command 'git') {
            & git clone --depth 1 --branch $reference $url $source
            if ($LASTEXITCODE -ne 0) { Stop-Install "git clone of $url failed" }
        }
        else {
            $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hardy-src-" + [System.Guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Force -Path $staging | Out-Null
            try {
                $archive = Join-Path $staging 'hardy.zip'
                Invoke-WebRequest -Uri "$url/archive/refs/heads/$reference.zip" -OutFile $archive -UseBasicParsing
                Expand-Archive -Path $archive -DestinationPath $staging -Force
                # GitHub archives wrap everything in a <repo>-<ref> directory.
                $extracted = Get-ChildItem -Directory $staging | Select-Object -First 1
                if (-not $extracted) { Stop-Install "the downloaded archive from $url was empty" }
                Copy-Item -Path (Join-Path $extracted.FullName '*') -Destination $source -Recurse -Force
            }
            finally { Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue }
        }
    }
    if (-not (Test-Path (Join-Path $source 'pyproject.toml'))) { Stop-Install "$source does not look like the Hardy repository" }
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
        & $venvPython -m pip install $wheel
        if ($LASTEXITCODE -ne 0) { Stop-Install "could not install $(Split-Path -Leaf $wheel) into $Venv" }
        Write-Detail "installed hardy from $(Split-Path -Leaf $wheel)"
        Remove-Item -Recurse -Force $directory -ErrorAction SilentlyContinue
        Save-ReleaseOrigin
    }
    else {
        & $venvPython -m pip install -e $RepoRoot
        if ($LASTEXITCODE -ne 0) { Stop-Install 'pip install failed' }
        Write-Detail "installed hardy (editable, from $RepoRoot)"
    }
    if (-not (Test-Path (Join-Path $Venv 'Scripts\hardy.exe'))) { Stop-Install "the hardy command was not installed into $Venv" }
}

function Add-Shim {
    Write-Step "Linking the hardy command into $BinDir"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $target = Join-Path $Venv 'Scripts\hardy.exe'
    Set-Content -Path (Join-Path $BinDir 'hardy.cmd') -Encoding ASCII -Value @"
@echo off
"$target" %*
"@
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -notlike "*$BinDir*") {
        [Environment]::SetEnvironmentVariable('Path', "$BinDir;$userPath", 'User')
        Write-Detail "added $BinDir to your user PATH (new terminals only)"
    }
    Update-SessionPath
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
    $architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
    $asset = "elan-$architecture-pc-windows-msvc.zip"
    $url = "https://github.com/leanprover/elan/releases/latest/download/$asset"
    $staging = Join-Path ([System.IO.Path]::GetTempPath()) ("hardy-elan-" + [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    try {
        Write-Detail "downloading $url"
        $archive = Join-Path $staging $asset
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
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
        & lake env lean $probe *> $null
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
        Write-Detail 'creating a Lake project pinned to Mathlib toolchain'
        Push-Location $LeanProject
        try {
            & lake "+$MathlibToolchain" init $LeanPackage math
            if ($LASTEXITCODE -ne 0) { Stop-Install 'lake init failed; see the output above' }
        }
        finally { Pop-Location }
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
    if ($FullLatex) {
        Install-WithWinget 'TeXLive.TeXLive' 'TeX Live'
    }
    else {
        # MiKTeX is the small option: it fetches LaTeX packages on demand.
        Install-WithWinget 'MiKTeX.MiKTeX' 'MiKTeX'
        if (Test-Command 'initexmf') {
            & initexmf --set-config-value '[MPM]AutoInstall=1' 2>$null
        }
    }
    Update-SessionPath
    if (-not (Test-Command 'pdflatex')) {
        Stop-Install 'pdflatex is still not on PATH after installing TeX; open a new PowerShell window and re-run'
    }
    Write-Detail "installed $((& pdflatex --version | Select-Object -First 1))"
}

function ConvertTo-TomlString($value) { $value -replace '\\', '\\\\' -replace '"', '\"' }

function Write-Config {
    if ($NoConfig) {
        Write-Step 'Skipping the config file (-NoConfig)'
        return
    }
    Write-Step "Writing $ConfigPath"
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
    } elseif (Get-Command npm -ErrorAction SilentlyContinue) {
        Write-Detail 'installing @anthropic-ai/claude-code'
        npm install -g @anthropic-ai/claude-code 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Warn "npm could not install @anthropic-ai/claude-code; install it yourself" }
    } else {
        # Node is not Hardy's to install, and guessing a package manager here
        # would be worse than saying plainly what is missing.
        Write-Warn "Node.js/npm not found: install Node, then 'npm install -g @anthropic-ai/claude-code'"
    }
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        $status = (claude auth status 2>$null) -join ''
        if ($status -notmatch '"loggedIn"\s*:\s*true') { Write-Detail "run 'claude login' to sign in with your subscription" }
    }
}

function Test-Installation {
    Write-Step 'Verifying the installation'
    $hardy = Join-Path $Venv 'Scripts\hardy.exe'
    $env:HARDY_CONFIG = $ConfigPath
    $hasModel = $ConfiguredModel -or $env:HARDY_MODEL -or
        ((Test-Path $ConfigPath) -and (Select-String -Path $ConfigPath -Pattern '^\s*model' -Quiet))
    # doctor checks the whole installation, so its verdict is only binding when
    # nothing was deliberately skipped.
    $strict = $hasModel -and -not $SkipLatex -and -not $SkipMathlib
    & $hardy doctor
    if ($LASTEXITCODE -ne 0) {
        if ($strict) { Stop-Install 'hardy doctor reported failures (see above)' }
        if (-not $hasModel) { Write-Warn "no model configured yet: add one to $ConfigPath or set HARDY_MODEL" }
        Write-Warn 'some checks did not pass; see what was skipped below'
    }
}

function Write-Summary {
    Write-Host "`nHardy is installed." -ForegroundColor Green
    Write-Host @"

  command      $(Join-Path $BinDir 'hardy.cmd')
  environment  $Venv
  installed    $(if ($script:InstallFrom -eq 'source') { "editable, from $RepoRoot" } else { 'from the published release' })
  lean project $LeanProject$(if ($SkipMathlib) { ' (skipped)' })
  config       $ConfigPath

Start doing mathematics with an agent:

  hardy

Other useful commands:

  hardy doctor --deep     check Lean, Mathlib, LaTeX, and the model end to end
  hardy chat --workspace .\my-project

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
Install-Elan
Install-LeanProject
Install-Latex
Write-Config
Install-ClaudeCli
Test-Installation
Write-Summary
