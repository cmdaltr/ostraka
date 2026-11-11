<#
Remove Ostraka from Windows (desktop/README.md).

    powershell -ExecutionPolicy Bypass -File uninstall.ps1             remove the app; %USERPROFILE%\Ostraka and its
                                                                       data stay for a reinstall
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Data       also stop the local stack, delete its Docker
                                                                       volumes (the database: every incident on this
                                                                       computer) and %USERPROFILE%\Ostraka, asking first
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Data -Yes  the same without asking

Without a console to ask on, -Data keeps everything unless -Yes says otherwise. Only a folder the
app unpacked (it has .ostraka-version) is deleted: never a git checkout, never anything else.
IT's policy key (HKLM\SOFTWARE\Policies\cmdaltr\Ostraka) is never touched. The program itself is
removed by Windows Installer, as Settings > Apps would. Works in Windows PowerShell 5.1 and pwsh.
#>
param(
    [switch]$Data,
    [switch]$Yes
)
$ErrorActionPreference = "Stop"

$script:step = 0
$script:steps = $(if ($Data) { 3 } else { 2 })
$script:t0 = Get-Date
$started = Get-Date
function Step($text) {
    $script:step++
    $script:t0 = Get-Date
    Write-Host ""
    Write-Host ("[{0}/{1}] {2}" -f $script:step, $script:steps, $text) -ForegroundColor Cyan
}
function Done($text) {
    Write-Host ("      OK  {0} ({1}s)" -f $text, [int]((Get-Date) - $script:t0).TotalSeconds) -ForegroundColor Green
}
function Note($text) { Write-Host "      $text" -ForegroundColor DarkGray }

$ostrakaHome = if ($env:OSTRAKA_HOME) { $env:OSTRAKA_HOME } else { Join-Path $env:USERPROFILE "Ostraka" }

Write-Host ""
Write-Host "  Ostraka: uninstaller"

Step "Removing the app"
# A per-user install registers under HKCU; look in HKLM too in case IT deployed it per-machine
$keys = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$entries = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -eq "Ostraka" -and $_.PSChildName -match '^\{[0-9A-Fa-f-]+\}$' })
if (-not $entries) {
    Done "Ostraka isn't installed"
} else {
    foreach ($e in $entries) {
        $log = Join-Path $env:TEMP "ostraka-uninstall.log"
        Note "Windows shows its own progress window while it removes Ostraka $($e.DisplayVersion)"
        $p = Start-Process msiexec.exe -ArgumentList "/x", $e.PSChildName, "/passive", "/l*v", "`"$log`"" -Wait -PassThru
        # 1605: already gone; 3010: removed, a restart is pending for something else
        if ($p.ExitCode -notin 0, 1605, 3010) {
            Get-Content $log -Tail 40
            throw "msiexec failed with $($p.ExitCode)"
        }
        Note "Removed Ostraka $($e.DisplayVersion)"
    }
    Done "Removed"
}
# The saved server (client mode) is the user's own setting and goes with the app
$saved = Join-Path $env:APPDATA "Ostraka\server"
if (Test-Path $saved) {
    Remove-Item -Force $saved
    $dir = Split-Path $saved
    if (-not (Get-ChildItem -Force $dir)) { Remove-Item -Force $dir }
    Note "Forgot the saved server"
}

if (-not $Data) {
    Step "Ostraka's data"
    if (Test-Path $ostrakaHome) { Done "Kept $ostrakaHome and the stack's volumes: uninstall.ps1 -Data deletes them" }
    else { Done "None at $ostrakaHome" }
    $took = (Get-Date) - $started
    Write-Host ""
    Write-Host ("  Ostraka is uninstalled ({0}m {1}s)." -f [math]::Floor($took.TotalMinutes), $took.Seconds) -ForegroundColor Green
    exit 0
}

# docker writes progress to stderr, which Windows PowerShell 5.1 turns into errors under Stop
function Invoke-Docker {
    $ErrorActionPreference = "Continue"
    & docker @args 2>$null
    $ErrorActionPreference = "Stop"
}

# Only a tree the app unpacked (it writes .ostraka-version) is ever deleted, so OSTRAKA_HOME pointing
# at a profile folder or anything else can't take it with it. A checkout keeps its folder, as below.
$checkout = Test-Path -LiteralPath (Join-Path $ostrakaHome ".git")
$marker = Join-Path $ostrakaHome ".ostraka-version"
if ((Test-Path -LiteralPath $ostrakaHome) -and -not $checkout -and -not (Test-Path -LiteralPath $marker)) {
    [Console]::Error.WriteLine("uninstall.ps1: $ostrakaHome has no .ostraka-version, so it isn't a folder the Ostraka app unpacked: nothing in it is deleted. Remove it yourself if it's Ostraka's.")
    exit 1
}

# The project the host ostraka command uses: COMPOSE_PROJECT_NAME in .env, else ostraka-app for an
# unpacked tree, else the folder's name (a checkout). Not the shell's own COMPOSE_PROJECT_NAME: a
# stray one would point down -v at some other stack's volumes.
$project = ''
$envFile = Join-Path $ostrakaHome ".env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in [IO.File]::ReadAllLines($envFile)) {
        if ($line.StartsWith("COMPOSE_PROJECT_NAME=")) { $project = $line.Substring(21).Trim().Trim('"', "'") }
    }
}
if (-not $project) {
    if (Test-Path -LiteralPath $marker) { $project = "ostraka-app" }
    else { $project = (Split-Path $ostrakaHome -Leaf).ToLower() }
}
$env:COMPOSE_PROJECT_NAME = $project

Step "Stopping the local stack"
$compose = $false
$blocked = $false
$volumes = @()
$hasDocker = [bool](Get-Command docker -ErrorAction SilentlyContinue)
if ($hasDocker) { Invoke-Docker info | Out-Null; $hasDocker = ($LASTEXITCODE -eq 0) }
if ((Test-Path (Join-Path $ostrakaHome "docker-compose.yml")) -and $hasDocker) {
    $compose = $true
    Push-Location $ostrakaHome
    try {
        # Listed by label rather than guessed from their names
        $volumes = @(Invoke-Docker volume ls -q --filter "label=com.docker.compose.project=$project" | Where-Object { $_ })
        Invoke-Docker compose stop | Out-Null
    }
    finally { Pop-Location }
    Done "Stopped (project $project)"
} elseif (Test-Path (Join-Path $ostrakaHome "docker-compose.yml")) {
    # The folder stays with them: it's what finds the volumes again once Docker is back
    $blocked = $true
    Done "Docker isn't running, so the stack's volumes can't be removed now"
} else {
    Done "No stack at $ostrakaHome"
}

Step "Deleting Ostraka's data"
if (-not (Test-Path $ostrakaHome) -and -not $volumes) {
    Done "Nothing to delete"
} else {
    if ($volumes) { Note "Docker volumes: $($volumes -join ' ')" }
    if ($blocked) { Note "$ostrakaHome stays until Docker Desktop is running and this runs again, so its volumes can go with it" }
    if ((Test-Path $ostrakaHome) -and -not $checkout -and -not $blocked) {
        $bytes = (Get-ChildItem $ostrakaHome -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
        Note ("Folder: {0} ({1:N0} MB)" -f $ostrakaHome, ($bytes / 1MB))
    }
    if ($checkout) { Note "$ostrakaHome is a git checkout: it stays, only the stack's volumes go" }
    $delete = [bool]$Yes
    if (-not $Yes) {
        if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
            $answer = Read-Host "      Delete these: every incident, user and setting this computer holds? It can't be undone. [y/N]"
            $delete = $answer -match '^[yY]'
        } else {
            Note "No console to ask on, so nothing is deleted (-Yes deletes without asking)"
        }
    }
    if ($delete) {
        if ($compose) {
            Push-Location $ostrakaHome
            # down -v removes the containers, the network and the named volumes; --rmi local the images
            # this stack built (backend and web), which are hundreds of megabytes
            try { Invoke-Docker compose down -v --rmi local --remove-orphans | Out-Null }
            finally { Pop-Location }
            if ($LASTEXITCODE -ne 0) { Note "docker compose down didn't finish: docker volume ls shows what's left" }
        }
        # Checked again just before: nothing without the marker is ever removed
        if (-not $checkout -and -not $blocked -and (Test-Path -LiteralPath $marker)) {
            Remove-Item -LiteralPath $ostrakaHome -Recurse -Force
        }
        Done "Deleted"
    } else {
        Done "Kept everything"
    }
}

$took = (Get-Date) - $started
Write-Host ""
Write-Host ("  Ostraka is uninstalled ({0}m {1}s)." -f [math]::Floor($took.TotalMinutes), $took.Seconds) -ForegroundColor Green
