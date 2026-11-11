<#
.SYNOPSIS
    Install (or -Uninstall) Ostraka's Start menu shortcuts for a checkout, for the current user.
.DESCRIPTION
    The same four shortcuts the MSI makes, in Start > Ostraka, pointing at this checkout's
    Ostraka.ps1. A checkout runs its own tree (it's never unpacked over), so it's always local
    unless a server is set. Windows doesn't let a script pin to the taskbar: right-click Ostraka in
    the Start menu and choose Pin to taskbar. 'ostraka app install' runs this.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File app\windows\install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File app\windows\install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Folder = Join-Path ([Environment]::GetFolderPath('Programs')) 'Ostraka'
# Name, Ostraka.ps1 switch, window style (1 normal, 7 minimised), description
$Shortcuts = @(
    @('Ostraka', '', 1, 'Incident management and response'),
    @('Stop Ostraka', '-Stop', 7, 'Stop Ostraka on this computer (your data stays)'),
    @('Change Server', '-ChangeServer', 1, 'Connect Ostraka to another server, or run it here'),
    @('Ostraka Licence', '-Licence', 7, 'Ostraka''s licence and commercial terms')
)

if ($Uninstall) {
    foreach ($s in $Shortcuts) {
        $link = Join-Path $Folder "$($s[0]).lnk"
        if (Test-Path $link) { Remove-Item $link; Write-Host "Removed $link" }
    }
    # Only if it's empty: the MSI's shortcuts live here too
    if ((Test-Path $Folder) -and -not @(Get-ChildItem -Force $Folder).Count) { Remove-Item $Folder }
    exit 0
}

$Launcher = Join-Path $PSScriptRoot 'Ostraka.ps1'
$Icon = Join-Path $Root 'app\icons\ostraka.ico'
New-Item -ItemType Directory -Force -Path $Folder | Out-Null
$Shell = New-Object -ComObject WScript.Shell
foreach ($s in $Shortcuts) {
    $link = Join-Path $Folder "$($s[0]).lnk"
    $shortcut = $Shell.CreateShortcut($link)
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`" $($s[1])".TrimEnd()
    $shortcut.WorkingDirectory = $Root
    if (Test-Path $Icon) { $shortcut.IconLocation = "$Icon,0" }
    $shortcut.WindowStyle = $s[2]
    $shortcut.Description = $s[3]
    $shortcut.Save()
    Write-Host "Created $link"
}
if (-not (Test-Path $Icon)) { Write-Host "No icon at $Icon yet: the shortcuts use PowerShell's (desktop/icons/build.sh makes it)." }
Write-Host 'Pin it: right-click Ostraka in the Start menu > Pin to taskbar.'
