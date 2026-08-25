<#
.SYNOPSIS
    Removes Hardy from Windows. No WSL required.

.DESCRIPTION
    Takes out the virtual environment, the fetched source tree, the `hardy`
    command, and the PATH entry the installer added. Run it from PowerShell:

        powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1

    Anything expensive to rebuild or personal is asked about first: the Lean
    project, the config file, and elan. With -Yes and no other switch, the
    answer to each of those is no.

    MiKTeX, TeX Live, Node, and the Claude Code CLI are never removed. Hardy may
    have installed them, but they are ordinary shared tools.

.PARAMETER Yes
    Non-interactive: keep whatever was not asked for by switch.

.PARAMETER All
    Also remove the Lean project, the config file, and elan.

.PARAMETER RemoveLeanProject
    Also remove the shared Mathlib project.

.PARAMETER RemoveConfig
    Also remove the config file.

.PARAMETER RemoveToolchain
    Also remove elan and the Lean toolchain.

.PARAMETER Prefix
    Where Hardy was installed.

.PARAMETER BinDir
    Where the `hardy` command was placed.
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$All,
    [switch]$RemoveLeanProject,
    [switch]$RemoveConfig,
    [switch]$RemoveToolchain,
    [string]$Prefix = (Join-Path $env:LOCALAPPDATA 'hardy'),
    [string]$BinDir = (Join-Path $env:LOCALAPPDATA 'hardy\bin')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Venv = Join-Path $Prefix 'venv'
$LeanProject = Join-Path $Prefix 'lean'
$SourceTree = Join-Path $Prefix 'src'
$ElanHome = Join-Path $env:USERPROFILE '.elan'
$ConfigPath = if ($env:HARDY_CONFIG) { $env:HARDY_CONFIG } else { Join-Path $HOME '.hardy\config.toml' }
$script:Removed = 0
$script:Kept = 0

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Detail($message) { Write-Host "    $message" }

function Confirm-Step($question) {
    if ($Yes) { return $true }
    if (-not [Environment]::UserInteractive) { return $true }
    $reply = Read-Host "$question [y/N]"
    return ($reply -match '^(y|yes)$')
}

# Removing twice, or after an install that stopped halfway, has to end cleanly
# rather than fail on the first missing directory.
function Remove-Part($what, $path) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
        Write-Detail "removed $what ($path)"
        $script:Removed++
    }
}

function Keep-Part($what, $path) {
    Write-Detail "keeping $what ($path)"
    $script:Kept++
}

# A question worth asking only when there is something to remove. -Yes must not
# mean yes to these, so the switch decides before any prompt is reached.
function Test-Wanted($requested, $question, $path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    if ($requested) { return $true }
    if ($Yes) { return $false }
    return (Confirm-Step $question)
}

function Get-FolderSize($path) {
    try {
        $bytes = (Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        if (-not $bytes) { return 'unknown size' }
        return '{0:N1} GB' -f ($bytes / 1GB)
    }
    catch { return 'unknown size' }
}

function Remove-PathEntry {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { return }
    $kept = @($userPath -split ';' | Where-Object { $_ -and ($_.TrimEnd('\') -ne $BinDir.TrimEnd('\')) })
    if ($kept.Count -eq ($userPath -split ';' | Where-Object { $_ }).Count) { return }
    [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
    Write-Detail "removed $BinDir from your user PATH"
    $script:Removed++
}

Write-Step 'Removing Hardy'
Write-Detail "prefix: $Prefix"

if ($All) {
    $RemoveLeanProject = $true
    $RemoveConfig = $true
    $RemoveToolchain = $true
}

Remove-Part 'the hardy command' (Join-Path $BinDir 'hardy.cmd')
Remove-Part 'the virtual environment' $Venv
Remove-Part 'the fetched source tree' $SourceTree
# What a release install leaves behind instead of a source tree.
Remove-Part 'the fetched installers' (Join-Path $Prefix 'installers')
Remove-Part 'an interrupted download' (Join-Path $Prefix 'download')
Remove-Part 'an interrupted installer refresh' (Join-Path $Prefix 'installers.new')
# An update interrupted mid-swap leaves this behind, and it is a runnable
# installer bundle: reporting success with one still on disk would be a lie.
Remove-Part 'the installers an update displaced' (Join-Path $Prefix 'installers.previous')
Remove-Part 'the recorded release origin' (Join-Path $Prefix 'release-origin')
Remove-PathEntry

if (Test-Wanted $RemoveLeanProject "Remove the Lean project at $LeanProject ($(Get-FolderSize $LeanProject))? Rebuilding it is a multi-gigabyte download." $LeanProject) {
    Remove-Part 'the Lean project' $LeanProject
}
elseif (Test-Path -LiteralPath $LeanProject) { Keep-Part 'the Lean project' $LeanProject }

if (Test-Wanted $RemoveConfig "Remove the config file at ${ConfigPath}? It holds your model choice." $ConfigPath) {
    Remove-Part 'the config' $ConfigPath
}
elseif (Test-Path -LiteralPath $ConfigPath) { Keep-Part 'the config' $ConfigPath }

if (Test-Wanted $RemoveToolchain "Remove elan and the Lean toolchain at ${ElanHome}? Other Lean projects on this machine use it too." $ElanHome) {
    Remove-Part 'elan and the Lean toolchain' $ElanHome
}
elseif (Test-Path -LiteralPath $ElanHome) { Keep-Part 'elan and the Lean toolchain' $ElanHome }

# Only when empty: -Prefix may name a directory that was not Hardy's alone.
if ((Test-Path -LiteralPath $Prefix) -and -not (Get-ChildItem -Force -LiteralPath $Prefix)) {
    Remove-Item -LiteralPath $Prefix -Force -ErrorAction SilentlyContinue
    Write-Detail "removed $Prefix"
}
if ((Test-Path -LiteralPath $BinDir) -and -not (Get-ChildItem -Force -LiteralPath $BinDir)) {
    Remove-Item -LiteralPath $BinDir -Force -ErrorAction SilentlyContinue
}

Write-Host ''
if ($script:Removed -eq 0 -and $script:Kept -eq 0) {
    Write-Detail "nothing to remove: Hardy was not installed at $Prefix"
}
else {
    Write-Host 'Hardy is uninstalled.' -ForegroundColor Green
    if ($script:Kept -gt 0) { Write-Detail "$($script:Kept) item(s) kept, listed above; -All removes them." }
}
Write-Detail 'MiKTeX, TeX Live, Node, and the Claude Code CLI were left alone.'
Write-Host 'Open a new terminal so the removed PATH entry stops applying.'
