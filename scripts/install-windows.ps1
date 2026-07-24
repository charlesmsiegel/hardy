<#
.SYNOPSIS
    One-shot Hardy install for Windows. No WSL required.

.DESCRIPTION
    Takes a clean Windows machine to a working `hardy` command: Python, the Lean
    toolchain (elan/lake), a Mathlib project, pdflatex, and Hardy itself. Run it
    from PowerShell in the repository:

        powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1

    Winget installs Python, Git, and MiKTeX; elan comes from its official
    release, exactly as on Linux and macOS.

.PARAMETER Yes
    Non-interactive: accept every install and skip the configuration prompts.

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
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'hardy'),
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA 'hardy\bin')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { '' }
$Venv = Join-Path $Prefix 'venv'
$LeanProject = Join-Path $Prefix 'lean'
$LeanPackage = 'hardymath'
$MathlibToolchain = 'leanprover-community/mathlib4:lean-toolchain'
$ConfigPath = if ($env:HARDY_CONFIG) { $env:HARDY_CONFIG } else { Join-Path $env:APPDATA 'hardy\config.toml' }
$ConfiguredModel = ''
$Python = ''

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Detail($message) { Write-Host "    $message" }
function Write-Warn($message) { Write-Warning $message }
function Stop-Install($message) { Write-Host "error: $message" -ForegroundColor Red; exit 1 }
function Test-Command($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

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

# Installing Hardy means installing this source tree, so a copy of the script
# downloaded on its own (or run through `iex`) fetches the repository and
# re-execs from there.
function Initialize-Repository {
    if ($RepoRoot -and (Test-Path (Join-Path $RepoRoot 'pyproject.toml'))) { return $false }
    $url = if ($env:HARDY_REPO_URL) { $env:HARDY_REPO_URL } else { 'https://github.com/charlesmsiegel/hardy' }
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
    & $venvPython -m pip install -e $RepoRoot
    if ($LASTEXITCODE -ne 0) { Stop-Install 'pip install failed' }
    if (-not (Test-Path (Join-Path $Venv 'Scripts\hardy.exe'))) { Stop-Install "the hardy command was not installed into $Venv" }
    Write-Detail "installed hardy (editable, from $RepoRoot)"
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
    Set-Content -Path $probe -Encoding UTF8 -Value "import Mathlib`n`nexample : 2 + 2 = 4 := by norm_num"
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
            Add-Content -Path $ConfigPath -Value ('lean_project = "{0}"' -f (ConvertTo-TomlString $LeanProject))
            Write-Detail "recorded lean_project = $LeanProject"
        }
        return
    }
    $model = if ($env:HARDY_MODEL) { $env:HARDY_MODEL } else { '' }
    $key = if ($env:OPENAI_API_KEY) { $env:OPENAI_API_KEY } else { '' }
    $anthropicKey = if ($env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY } else { '' }
    $baseUrl = if ($env:HARDY_BASE_URL) { $env:HARDY_BASE_URL } else { '' }
    $backend = if ($env:HARDY_BACKEND) { $env:HARDY_BACKEND } else { '' }
    if (-not $model -and -not $Yes -and [Environment]::UserInteractive) {
        Write-Host "`nHardy talks to Claude through the Anthropic Messages API, and to any"
        Write-Host 'OpenAI-compatible endpoint with native tool calling. The backend follows'
        Write-Host 'the model identity, and /model switches between them later.'
        $model = Read-Host 'Model identity (e.g. claude-opus-5 or gpt-5.1; blank to skip)'
        if ($model -like 'claude-*') {
            $secret = Read-Host 'Anthropic API key (blank to read $ANTHROPIC_API_KEY at run time)' -AsSecureString
            $anthropicKey = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret))
        } elseif ($model) {
            $baseUrl = Read-Host 'API base URL [https://api.openai.com/v1]'
            $secret = Read-Host 'API key (blank to read $OPENAI_API_KEY at run time)' -AsSecureString
            $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret))
        }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ConfigPath) | Out-Null
    $lines = @(
        '# Written by the Hardy installer. Every value can be overridden by a',
        '# HARDY_* environment variable or a command-line flag.'
    )
    if ($model) { $lines += 'model = "{0}"' -f (ConvertTo-TomlString $model) }
    # An explicit pin must outlive the installer: without it Hardy infers the
    # backend from the identity, which is the case the pin exists to correct.
    if ($backend) { $lines += 'backend = "{0}"' -f (ConvertTo-TomlString $backend) }
    if ($baseUrl) { $lines += 'base_url = "{0}"' -f (ConvertTo-TomlString $baseUrl) }
    if ($key) { $lines += 'api_key = "{0}"' -f (ConvertTo-TomlString $key) }
    if ($anthropicKey) { $lines += 'anthropic_api_key = "{0}"' -f (ConvertTo-TomlString $anthropicKey) }
    if (-not $SkipMathlib) { $lines += 'lean_project = "{0}"' -f (ConvertTo-TomlString $LeanProject) }
    Set-Content -Path $ConfigPath -Value $lines -Encoding UTF8
    $script:ConfiguredModel = $model
    Write-Detail "wrote $ConfigPath"
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
Initialize-Repository | Out-Null
Write-Detail "repository: $RepoRoot"
Install-Prerequisites
New-Environment
Add-Shim
Install-Elan
Install-LeanProject
Install-Latex
Write-Config
Test-Installation
Write-Summary
