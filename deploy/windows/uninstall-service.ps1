<#
.SYNOPSIS
    Remove the Ostraka team server that deploy/windows/install-service.ps1 set up. Keeps data by default.

.DESCRIPTION
    Elevated PowerShell:
        .\deploy\windows\uninstall-service.ps1 [-Purge [-Yes]]

    Removes the boot task, the port proxy and its firewall rule, and %ProgramData%\Ostraka, then runs
    deploy/linux/uninstall.sh inside the distribution, which stops the stack. -Purge also removes the
    stack's volumes (every incident) and .env, after asking unless -Yes is given. The code dir inside
    the distribution, Docker and the distribution itself are always left in place.

.PARAMETER Distribution
    The WSL distribution Ostraka runs in (default: the one install-service.ps1 recorded).

.PARAMETER Dir
    The source tree inside it (default: the one recorded, or /opt/ostraka).
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string] $Distribution = '',
    [string] $Dir = '',
    [string] $TaskName = 'Ostraka',
    [switch] $Purge,
    [switch] $Yes
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Fail([string] $Text) { [Console]::Error.WriteLine("uninstall-service.ps1: $Text"); exit 1 }

if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) { Fail 'This is for Windows. On Linux run deploy/linux/uninstall.sh.' }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail 'Run this in an elevated PowerShell (Run as administrator).'
}

$StateDir = Join-Path $env:ProgramData 'Ostraka'
$StateFile = Join-Path $StateDir 'server.json'
$state = $null
if (Test-Path $StateFile) { $state = Get-Content -Raw $StateFile | ConvertFrom-Json }
if (-not $Distribution -and $state) { $Distribution = $state.Distribution }
if (-not $Dir) { $Dir = if ($state) { $state.Dir } else { '/opt/ostraka' } }
if (-not $Distribution) { Fail 'No record of the distribution Ostraka runs in: give -Distribution (wsl --list --verbose).' }

# The Linux side first, so a purge that's declined leaves everything as it was. The uninstaller beside
# this script is used, so the two match whatever version the server tree is.
$env:WSL_UTF8 = '1'
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$linux = (Resolve-Path (Join-Path $PSScriptRoot '..\linux\uninstall.sh')).Path
$wslPath = (((& wsl.exe -d $Distribution -- wslpath -a $linux 2>&1) | ForEach-Object { "$_" }) -join '').Trim()
$reached = ($LASTEXITCODE -eq 0 -and $wslPath.StartsWith('/'))
$ErrorActionPreference = $previous
if ($reached) {
    $arguments = @('--dir', $Dir)
    if ($Purge) { $arguments += '--purge' }
    if ($Yes) { $arguments += '--yes' }
    # Not captured, so --purge can ask on the console.
    & wsl.exe -d $Distribution -u root -- bash $wslPath @arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Warning "Couldn't reach $Distribution, so the stack wasn't stopped: run deploy/linux/uninstall.sh inside it later."
}

Write-Host "==> removing Scheduled Task '$TaskName'"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

if ($state -and $state.PortProxy) {
    Write-Host '==> removing the port proxy and its firewall rule'
    foreach ($port in $state.Ports) {
        & netsh.exe interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
    }
    Remove-NetFirewallRule -Name 'Ostraka-HTTPS' -ErrorAction SilentlyContinue
}
if (Test-Path $StateDir) { Remove-Item -Recurse -Force $StateDir }
Write-Host "    left $Distribution, Docker in it and the code in $Dir."
