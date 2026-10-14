<#
Native full-install acceptance. A hosted runner is NOT a clean Windows VM:
Python, Git and Node are prerequisites here, recorded rather than credited to
Hardy's installer. Mathlib, elan, Tectonic, Claude CLI, config and launchers must
be fresh. No model request or subscription sign-in is performed.
Run from a checkout with a locally built release served at ReleaseBaseUrl.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Root,
    [string]$ReleaseBaseUrl = 'http://127.0.0.1:8000',
    [switch]$Worker
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'This acceptance requires native Windows.' }
$Root = [IO.Path]::GetFullPath($Root)
$profile = Join-Path $Root 'profile'
$logs = Join-Path $Root 'logs'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if ($Worker) {
    if (-not [Environment]::UserInteractive) { throw 'Read-Host acceptance requires an interactive Windows session.' }
    Set-Variable -Name HOME -Value $profile -Force
    $prefix = Join-Path $env:LOCALAPPDATA 'hardy'
    $configPath = Join-Path $profile '.hardy\config.toml'
    if ($env:HARDY_CONFIG -ne $configPath) { throw 'config isolation was lost' }
    $before = [ordered]@{ userInteractive = [Environment]::UserInteractive; profile = $profile }
    foreach ($tool in @('python', 'git', 'npm', 'lake', 'tectonic', 'claude')) {
        $command = Get-Command $tool -ErrorAction SilentlyContinue
        $before[$tool] = if ($command) { $command.Source } else { $null }
    }
    $before | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $logs 'before.json')
    foreach ($tool in @('python', 'git', 'npm')) {
        if (-not $before[$tool]) { throw "Hosted acceptance requires $tool; its provisioning is not tested here." }
    }
    foreach ($tool in @('lake', 'tectonic', 'claude')) {
        if ($before[$tool]) { throw "Preinstalled $tool would mask installer provisioning: $($before[$tool])" }
    }
    foreach ($path in @($prefix, (Join-Path $profile '.elan'), $configPath)) {
        if (Test-Path $path) { throw "Acceptance must start without $path" }
    }
    # The only install prompt is elan (Python/Git are recorded prerequisites),
    # followed by the model identity. No -Yes and no HARDY_MODEL bypass.
    $answers = Join-Path $Root 'answers.txt'
    [IO.File]::WriteAllText($answers, "y`r`nclaude-opus-5`r`n")
    . (Join-Path $PSScriptRoot 'read-windows-console.ps1')
    $beforeConsole = Read-AcceptanceConsole
    [IO.File]::WriteAllText((Join-Path $logs 'console.before.txt'), $beforeConsole)
    $beforeCells = $beforeConsole.Replace("`r", '').Replace("`n", '')
    foreach ($prompt in @('Install elan', 'Model identity')) {
        if ($beforeCells -match [regex]::Escape($prompt)) { throw "Preexisting console prompt would mask acceptance: $prompt" }
    }
    $installer = Join-Path $Root 'standalone\install-windows.ps1'
    $process = Start-Process -FilePath $powershell -PassThru -Wait -NoNewWindow `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$installer`"") `
        -RedirectStandardInput $answers `
        -RedirectStandardOutput (Join-Path $logs 'installer.stdout.txt') `
        -RedirectStandardError (Join-Path $logs 'installer.stderr.txt')
    # Snapshot before replaying redirected stdout: require evidence written by
    # the real Read-Host host UI, not strings copied from the installer log.
    $afterConsole = Read-AcceptanceConsole
    [IO.File]::WriteAllText((Join-Path $logs 'console.after.txt'), $afterConsole)
    Get-Content (Join-Path $logs 'installer.stdout.txt')
    Get-Content (Join-Path $logs 'installer.stderr.txt')
    if ($process.ExitCode -ne 0) { throw "full installer exited $($process.ExitCode)" }
    $output = Get-Content -Raw (Join-Path $logs 'installer.stdout.txt')
    # Rows are fixed-width cells. Join them for matching a prompt that wraps.
    $afterCells = $afterConsole.Replace("`r", '').Replace("`n", '')
    foreach ($prompt in @('Install elan', 'Model identity')) {
        if ($afterCells -notmatch [regex]::Escape($prompt)) { throw "The real $prompt prompt was not exercised." }
    }
    if ($output -notmatch 'Hardy is installed\.' -or $output -notmatch 'claude login') {
        throw 'Installer did not report installed software and pending subscription login.'
    }
    foreach ($required in @('venv\Scripts\hardy.exe', 'bin\hardy.cmd', 'installers\scripts\install-windows.ps1',
            'lean\lake-manifest.json')) {
        if (-not (Test-Path (Join-Path $prefix $required))) { throw "$required is missing" }
    }
    if (-not (Test-Path (Join-Path $profile '.elan\bin\lake.exe'))) { throw 'Fresh profile has no installed lake.exe' }
    foreach ($forbidden in @('src', 'download', 'installers.new')) {
        if (Test-Path (Join-Path $prefix $forbidden)) { throw "Unexpected $forbidden after release install" }
    }
    $shell = New-Object -ComObject WScript.Shell
    foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
        if (-not $folder.StartsWith($profile, [StringComparison]::OrdinalIgnoreCase)) { throw "Launcher folder was not isolated: $folder" }
        $path = Join-Path $folder 'Hardy.lnk'
        if (-not (Test-Path $path)) { throw "Missing launcher $path" }
        $shortcut = $shell.CreateShortcut($path)
        if ($shortcut.TargetPath -ne (Join-Path $prefix 'venv\Scripts\hardy.exe') -or
            $shortcut.Arguments -ne 'web --open' -or $shortcut.WorkingDirectory -ne $prefix) {
            throw "Invalid launcher $path"
        }
    }
    # Start with the PATH a new terminal receives, rather than borrowing the
    # installer's process-local additions. Include its isolated elan location.
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + (Join-Path $profile '.elan\bin')
    & (Join-Path $prefix 'bin\hardy.cmd') --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Installed command shim failed' }
    $probe = @'
import json, sys
from pathlib import Path
import hardy
from hardy.app.config import load
from hardy.app.doctor import run_checks
prefix = Path(sys.argv[1])
config = load()
assert "site-packages" in Path(hardy.__file__).resolve().parts
assert config.model == "claude-opus-5", config.model
assert config.lean_project == prefix / "lean", config.lean_project
assert Path(config.tectonic).resolve().is_relative_to(prefix / "venv" / "tools"), config.tectonic
assert config.latex_command[0] == str(config.tectonic), config.latex_command
checks = run_checks(config, deep=True)
print(json.dumps([dict(name=c.name, ok=c.ok, required=c.required, detail=c.detail) for c in checks], indent=2))
by_name = {c.name: c for c in checks}
for name in ("lean", "mathlib", "toolchain pin", "tectonic", "latex", "claude cli"):
    assert by_name[name].ok, by_name[name]
assert not by_name["claude login"].ok, "CI must not inherit subscription credentials"
assert {c.name for c in checks if c.required and not c.ok} == {"claude login"}
'@
    $probe | & (Join-Path $prefix 'venv\Scripts\python.exe') - $prefix |
        Tee-Object -FilePath (Join-Path $logs 'doctor.json')
    if ($LASTEXITCODE -ne 0) { throw 'Full doctor acceptance failed (only Claude login may remain)' }
    Write-Host 'Full native install passed; Claude login pending. Clean-OS prerequisite provisioning remains untested.'
    exit 0
}

if (Test-Path $Root) { throw "Use a fresh acceptance directory: $Root" }
New-Item -ItemType Directory -Path $logs, (Join-Path $Root 'standalone') -Force | Out-Null
Copy-Item (Join-Path (Split-Path $PSScriptRoot -Parent) 'install-windows.ps1') (Join-Path $Root 'standalone')
# Save registry values with their types. Only this user's PATH/known folders are
# changed; no machine package or machine registry is removed.
# Keep only the three declared hosted prerequisite directories from the
# current process's additions (setup-python does not update the machine PATH).
$prerequisiteDirs = foreach ($tool in @('python', 'git', 'npm')) {
    $command = Get-Command $tool -ErrorAction Stop
    Split-Path $command.Source -Parent
}
$fixturePath = (($prerequisiteDirs | Select-Object -Unique) -join ';') + ';' + (Join-Path $profile 'AppData\Roaming\npm')
$changes = @(
    @{ Key = 'Environment'; Name = 'Path'; Value = $fixturePath },
    @{ Key = 'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'; Name = 'Desktop'; Value = (Join-Path $profile 'Desktop') },
    @{ Key = 'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'; Name = 'Programs'; Value = (Join-Path $profile 'Programs') },
    @{ Key = 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'; Name = 'Desktop'; Value = (Join-Path $profile 'Desktop') },
    @{ Key = 'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'; Name = 'Programs'; Value = (Join-Path $profile 'Programs') }
)
$saved = @()
# Snapshot only names this fixture will change. Enumerate before mutation;
# PowerShell 5.1 can list empty environment entries that Get/Remove cannot find.
$clearedNames = @(Get-ChildItem Env: | Where-Object { $_.Name -match '^(HARDY_|ANTHROPIC_|CLAUDE|CODEX|OPENAI_|ELAN_|LAKE_|LEAN_|PYTHON|PIP_|npm_config_|NODE_)' } | ForEach-Object { $_.Name })
$modifiedNames = @($clearedNames) + @('USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP',
    'XDG_CACHE_HOME', 'ELAN_HOME', 'CLAUDE_CONFIG_DIR', 'HARDY_CONFIG', 'HARDY_RELEASE_BASE_URL',
    'npm_config_prefix', 'Path')
$environment = @{}
foreach ($name in ($modifiedNames | Select-Object -Unique)) {
    $environment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    foreach ($change in $changes) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($change.Key)
        try {
            $exists = $key.GetValueNames() -contains $change.Name
            $saved += @{ Key = $change.Key; Name = $change.Name; Exists = $exists;
                Value = $key.GetValue($change.Name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames);
                Kind = if ($exists) { $key.GetValueKind($change.Name) } else { [Microsoft.Win32.RegistryValueKind]::String } }
            $key.SetValue($change.Name, $change.Value, [Microsoft.Win32.RegistryValueKind]::String)
        } finally { $key.Close() }
    }
    foreach ($name in $clearedNames) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
    $env:USERPROFILE = $profile
    $env:HOME = $profile
    $env:APPDATA = Join-Path $profile 'AppData\Roaming'
    $env:LOCALAPPDATA = Join-Path $profile 'AppData\Local'
    $env:TEMP = Join-Path $profile 'Temp'
    $env:TMP = $env:TEMP
    $env:XDG_CACHE_HOME = Join-Path $profile '.cache'
    $env:ELAN_HOME = Join-Path $profile '.elan'
    $env:CLAUDE_CONFIG_DIR = Join-Path $profile '.claude'
    $env:HARDY_CONFIG = Join-Path $profile '.hardy\config.toml'
    $env:HARDY_RELEASE_BASE_URL = $ReleaseBaseUrl
    $env:npm_config_prefix = Join-Path $env:APPDATA 'npm'
    New-Item -ItemType Directory -Force -Path $env:APPDATA, $env:LOCALAPPDATA, $env:TEMP,
        (Join-Path $profile 'Desktop'), (Join-Path $profile 'Programs') | Out-Null
    # Machine Python/Git/Node remain visible, explicitly recorded in before.json.
    $env:Path = $fixturePath + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
    & $powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Root $Root -Worker
    if ($LASTEXITCODE -ne 0) { throw "Full acceptance worker exited $LASTEXITCODE" }
} finally {
    foreach ($value in $saved) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($value.Key)
        try {
            if ($value.Exists) { $key.SetValue($value.Name, $value.Value, $value.Kind) }
            else { $key.DeleteValue($value.Name, $false) }
        } finally { $key.Close() }
    }
    foreach ($name in $environment.Keys) { [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process') }
}
