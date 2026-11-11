<#
.SYNOPSIS
    The Ostraka app on Windows: what the Start menu shortcuts run.
.DESCRIPTION
    Opens Ostraka in its own Edge (or Chrome) app window, with a browser profile of its own. Ostraka
    is either a team's server, checked before it's opened, or the Docker stack on this computer,
    which this script unpacks, updates and starts through the host CLI, desktop\windows\ostraka-cli.ps1
    (desktop/README.md has the contract). The first launch asks which.

    Where the server comes from, first match wins:
      HKLM\SOFTWARE\Policies\cmdaltr\Ostraka ServerURL   Group Policy or Intune; locked
      HKCU\SOFTWARE\Policies\cmdaltr\Ostraka ServerURL   the same, per user; locked
      HKCU\SOFTWARE\cmdaltr\Ostraka ServerURL            the MSI's SERVERURL property; locked
      OSTRAKA_SERVER                                     a URL, or 'local'; not locked
      %APPDATA%\Ostraka\server                           what the first launch saved
    Locked means no first-run screen and no Change Server.
.PARAMETER Stop
    Stop Ostraka on this computer (docker compose stop: the data stays).
.PARAMETER ChangeServer
    Forget the saved choice and ask again.
.PARAMETER Licence
    Open the licence and commercial terms (<server>/help/licence, or the files beside the app).
.PARAMETER About
    Print the version and licence.
.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File Ostraka.ps1 -ChangeServer
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$ChangeServer,
    [switch]$Licence,
    [switch]$About
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

$AppDir = $PSScriptRoot
# An installed app has the source zip beside it; a checkout's desktop\windows is inside the tree it runs
$Packaged = Test-Path (Join-Path $AppDir 'source.zip')
# The host CLI's place in a source tree. Not ostraka.ps1: Windows and macOS filesystems ignore case,
# and this file is Ostraka.ps1
$CliPath = 'desktop\windows\ostraka-cli.ps1'
# 'ostraka first-admin' exits 5 when there's an admin already; any other failure is retried later
$ExitHasAdmin = 5
$DockerDownload = 'https://www.docker.com/products/docker-desktop/'
$PolicyKeys = @(
    @{ Path = 'HKLM:\SOFTWARE\Policies\cmdaltr\Ostraka'; Source = 'Group Policy (this computer)' },
    @{ Path = 'HKCU:\SOFTWARE\Policies\cmdaltr\Ostraka'; Source = 'Group Policy (your account)' },
    @{ Path = 'HKCU:\SOFTWARE\cmdaltr\Ostraka'; Source = 'the installer' }
)

function Get-AppPaths {
    <# Where this launcher reads and writes, from the environment (desktop/README.md, "Where things live"). #>
    $appData = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }
    $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME '.local/share' }
    $userProfile = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
    $ostrakaHome = if ($env:OSTRAKA_HOME) { $env:OSTRAKA_HOME }
                   elseif ($Packaged) { Join-Path $userProfile 'Ostraka' }
                   else { (Resolve-Path (Join-Path $AppDir '..\..')).Path }
    # %LOCALAPPDATA%\Ostraka, not ...\Programs\Ostraka: Windows Installer owns that folder
    $state = Join-Path $localAppData 'Ostraka'
    return [pscustomobject]@{
        ServerFile     = Join-Path (Join-Path $appData 'Ostraka') 'server'
        State          = $state
        BrowserProfile = Join-Path $state 'browser'
        AdminPending   = Join-Path $state 'first-admin-pending'
        Home           = $ostrakaHome
    }
}

function Get-AppVersion {
    <# The installed app's version, or a checkout's from the backend package. #>
    $file = Join-Path $AppDir 'version.txt'
    if (Test-Path $file) { return (Get-Content $file -Raw).Trim() }
    $init = Join-Path (Get-AppPaths).Home 'backend\ostraka\__init__.py'
    if ((Test-Path $init) -and ((Get-Content $init -Raw) -match '__version__\s*=\s*"([^"]+)"')) { return $Matches[1] }
    return '0.0.0'
}

# ── Which Ostraka ─────────────────────────────────────────────────────────────

function ConvertTo-ServerAddress {
    <#
    .SYNOPSIS
        A typed or configured server as scheme://host[:port], 'local', or $null if it isn't one.
        Anything after the host and port (a pasted incident link) is dropped: Ostraka is served
        from the root.
    .PARAMETER AllowHttp
        Plain http:// for any host: only for a server IT or OSTRAKA_SERVER set. Otherwise http is
        accepted for this computer alone.
    #>
    param([string]$Text, [switch]$AllowHttp)
    if ($null -eq $Text) { return $null }
    $Text = $Text.Trim()
    if (-not $Text) { return $null }
    if ($Text -eq 'local') { return 'local' }
    if ($Text -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]*://') { $Text = "https://$Text" }
    $uri = $null
    if (-not [Uri]::TryCreate($Text, [UriKind]::Absolute, [ref]$uri)) { return $null }
    if (-not $uri.Host -or $uri.UserInfo) { return $null }
    $scheme = $uri.Scheme.ToLowerInvariant()
    $loopback = @('localhost', '127.0.0.1', '[::1]') -contains $uri.Host.ToLowerInvariant()
    if ($scheme -ne 'https' -and -not ($scheme -eq 'http' -and ($AllowHttp -or $loopback))) { return $null }
    return "$scheme`://$($uri.Authority)"
}

function Get-PolicyServer {
    <# The first ServerURL in the locked keys, as @{ Value; Source }, or $null. An empty value is unset. #>
    foreach ($key in $PolicyKeys) {
        try {
            $value = (Get-ItemProperty -Path $key.Path -Name ServerURL -ErrorAction Stop).ServerURL
        } catch {
            continue
        }
        if ("$value".Trim()) { return @{ Value = "$value".Trim(); Source = $key.Source } }
    }
    return $null
}

function Get-ServerSetting {
    <#
    .SYNOPSIS
        Which Ostraka to open: Value is a URL, 'local' or '' (ask); Locked when IT set it.
    #>
    $policy = Get-PolicyServer
    if ($policy) {
        $value = ConvertTo-ServerAddress $policy.Value -AllowHttp
        if (-not $value) { throw "The Ostraka server $($policy.Source) set, '$($policy.Value)', isn't an address. Ask your IT team." }
        return [pscustomobject]@{ Value = $value; Locked = $true; Source = $policy.Source }
    }
    if ($env:OSTRAKA_SERVER) {
        $value = ConvertTo-ServerAddress $env:OSTRAKA_SERVER -AllowHttp
        if (-not $value) { throw "OSTRAKA_SERVER is '$env:OSTRAKA_SERVER': set it to a server's address or 'local'." }
        return [pscustomobject]@{ Value = $value; Locked = $false; Source = 'OSTRAKA_SERVER' }
    }
    $file = (Get-AppPaths).ServerFile
    if (Test-Path $file) {
        # Saved only after it passed the check, so the same rules hold
        $value = ConvertTo-ServerAddress (Get-Content $file -Raw)
        if ($value) { return [pscustomobject]@{ Value = $value; Locked = $false; Source = 'saved' } }
    }
    return [pscustomobject]@{ Value = ''; Locked = $false; Source = 'none' }
}

function Save-ServerSetting {
    <# Remember the first launch's choice: a URL or 'local'. #>
    param([string]$Value)
    $file = (Get-AppPaths).ServerFile
    New-Item -ItemType Directory -Force -Path (Split-Path $file) | Out-Null
    Set-Content -Path $file -Value $Value -Encoding ASCII
}

function Test-OstrakaServer {
    <#
    .SYNOPSIS
        Whether the address is an Ostraka server: GET /api/meta answers "name": "Ostraka".
    .OUTPUTS
        @{ Ok; Message; Version }. Over https the certificate must be one Windows trusts:
        Invoke-RestMethod checks it against the certificate store, and nothing here relaxes that.
    #>
    param([string]$Url, [int]$TimeoutSec = 10)
    try {
        $meta = Invoke-RestMethod -Uri "$Url/api/meta" -TimeoutSec $TimeoutSec -UseBasicParsing -Headers @{ Accept = 'application/json' }
    } catch {
        $detail = $_.Exception
        while ($detail.InnerException) { $detail = $detail.InnerException }
        $text = "$($_.Exception.Message) $($detail.Message)"
        if ($text -match 'trust|certificate|SSL|TLS|secure channel') {
            return @{ Ok = $false; Version = ''
                      Message = "$Url's certificate isn't one Windows trusts. Ask its admin for the root certificate to install." }
        }
        if ($_.Exception.PSObject.Properties['Response'] -and $_.Exception.Response) {
            return @{ Ok = $false; Version = ''; Message = "$Url answered, but it isn't an Ostraka server." }
        }
        return @{ Ok = $false; Version = ''; Message = "Couldn't reach $Url ($($detail.Message.Trim()))." }
    }
    if ($meta -isnot [System.Management.Automation.PSCustomObject] -or -not $meta.PSObject.Properties['name'] -or $meta.name -ne 'Ostraka') {
        return @{ Ok = $false; Version = ''; Message = "$Url answered, but it isn't an Ostraka server." }
    }
    $version = if ($meta.PSObject.Properties['version']) { "$($meta.version)" } else { '' }
    return @{ Ok = $true; Version = $version; Message = "Ostraka $version at $Url" }
}

function Read-FirstRunChoice {
    <# The first-run screen: a checked server address, or 'local'. Saves the choice. #>
    Write-Host ''
    Write-Host 'Ostraka' -ForegroundColor Cyan
    Write-Host 'Incident management and response. Using Ostraka is accepting its licence (Ostraka Licence in the Start menu).'
    Write-Host ''
    while ($true) {
        $choices = [System.Management.Automation.Host.ChoiceDescription[]]@(
            (New-Object System.Management.Automation.Host.ChoiceDescription '&Connect to your team''s Ostraka', 'A server address: nothing is stored or run on this computer.'),
            (New-Object System.Management.Automation.Host.ChoiceDescription '&Run Ostraka on this computer', 'Ostraka in Docker Desktop, on this computer only.'),
            (New-Object System.Management.Automation.Host.ChoiceDescription '&Quit', 'Close Ostraka.')
        )
        $pick = $Host.UI.PromptForChoice('How will you use Ostraka?', '', $choices, 0)
        if ($pick -eq 2) { exit 0 }
        if ($pick -eq 1) { Save-ServerSetting 'local'; return 'local' }
        while ($true) {
            $typed = Read-Host 'Server address (for example ostraka.example.org; blank to go back)'
            if (-not $typed.Trim()) { break }
            $url = ConvertTo-ServerAddress $typed
            if (-not $url -or $url -eq 'local') {
                Write-Host 'That isn''t an address Ostraka can use: it needs https:// (plain http only for this computer).' -ForegroundColor Yellow
                continue
            }
            Write-Host "Checking $url..."
            $check = Test-OstrakaServer $url
            if ($check.Ok) {
                Write-Host $check.Message -ForegroundColor Green
                Save-ServerSetting $url
                return $url
            }
            Write-Host $check.Message -ForegroundColor Yellow
        }
    }
}

# ── The browser window ────────────────────────────────────────────────────────

function Open-Window {
    <# The URL in an Edge (or Chrome) app window: no tabs or address bar, Ostraka's own profile. #>
    param([string]$Url)
    $profileDir = (Get-AppPaths).BrowserProfile
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    # Edge ships with Windows 10 and 11, under Program Files (x86) on x64 and ARM64 alike
    $browsers = @(
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
        "$env:LOCALAPPDATA\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $browser = $browsers | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($browser) {
        Start-Process $browser -ArgumentList "--app=$Url", "--user-data-dir=`"$profileDir`"", '--no-first-run', '--no-default-browser-check'
    } else {
        Start-Process $Url
    }
}

# ── Ostraka on this computer ──────────────────────────────────────────────────

function Get-DockerState {
    <# 'ok', 'stopped' (installed, engine not answering) or 'missing'. #>
    $desktop = Join-Path "$env:ProgramFiles" 'Docker\Docker\Docker Desktop.exe'
    $cli = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $cli -and -not (Test-Path $desktop)) { return 'missing' }
    if (-not $cli) { return 'stopped' }
    # Windows PowerShell turns a native command's stderr into a terminating error under 'Stop'
    $ErrorActionPreference = 'Continue'
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { return 'ok' }
    return 'stopped'
}

function Wait-Docker {
    <# Docker Desktop running, or a message saying what's missing. Never installs anything. #>
    $state = Get-DockerState
    if ($state -eq 'ok') { return }
    if ($state -eq 'missing') {
        Write-Host ''
        Write-Host 'Ostraka on this computer runs in Docker, and Docker Desktop isn''t installed.' -ForegroundColor Yellow
        Write-Host "Install Docker Desktop ($DockerDownload), start it once, then open Ostraka again."
        Write-Host 'It needs WSL 2, which its installer sets up. Docker Desktop is free for personal use and small'
        Write-Host 'businesses; larger organisations need a paid subscription. Or use Change Server to connect to'
        Write-Host 'your team''s Ostraka instead.'
        $answer = Read-Host 'Open the Docker Desktop download page? [Y/n]'
        if ($answer -notmatch '^[nN]') { Start-Process $DockerDownload }
        exit 1
    }
    $desktop = Join-Path "$env:ProgramFiles" 'Docker\Docker\Docker Desktop.exe'
    Write-Progress -Activity 'Starting Ostraka' -Status 'Starting Docker Desktop' -PercentComplete 0
    # Its own CLI starts the engine without the dashboard; older versions don't have it
    $started = $false
    if (Get-Command docker -ErrorAction SilentlyContinue) {
        $ErrorActionPreference = 'Continue'
        docker desktop start --detach *> $null
        $started = $LASTEXITCODE -eq 0
        $ErrorActionPreference = 'Stop'
    }
    if (-not $started -and (Test-Path $desktop)) { Start-Process $desktop -WindowStyle Hidden }
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Seconds 2
        if ((Get-DockerState) -eq 'ok') { return }
    }
    throw 'Docker Desktop didn''t start in four minutes. Start it from the Start menu, then open Ostraka again.'
}

function Compare-Version {
    <# -1, 0 or 1 as A is older than, the same as or newer than B; unparseable versions are equal. #>
    param([string]$A, [string]$B)
    $va = $null; $vb = $null
    if (-not [Version]::TryParse($A.Trim().TrimStart('v'), [ref]$va) -or
        -not [Version]::TryParse($B.Trim().TrimStart('v'), [ref]$vb)) { return 0 }
    return $va.CompareTo($vb)
}

function Find-SourceRoot {
    <# The source tree in an unpacked archive: the folder itself, or its one top-level folder
       (git archive --prefix=ostraka/). $null if neither holds docker-compose.yml. #>
    param([string]$Path)
    if (Test-Path (Join-Path $Path 'docker-compose.yml')) { return $Path }
    $dirs = @(Get-ChildItem -Force -Directory $Path)
    $files = @(Get-ChildItem -Force -File $Path)
    if ($dirs.Count -eq 1 -and -not $files.Count -and (Test-Path (Join-Path $dirs[0].FullName 'docker-compose.yml'))) {
        return $dirs[0].FullName
    }
    return $null
}

function Expand-Source {
    <#
    .SYNOPSIS
        Unpack the bundled source into OSTRAKA_HOME.
    .DESCRIPTION
        A copy this app unpacked before (it has .ostraka-version) is mirrored: files the new
        version doesn't carry are deleted, since a removed Alembic revision left behind would
        give two heads. .env, the version marker and .git are never touched, nor junctions,
        so nothing outside OSTRAKA_HOME is; the Docker volumes aren't in the folder at all.
    #>
    param([string]$Destination, [string]$Version)
    $paths = Get-AppPaths
    $staging = Join-Path $paths.State 'unpack'
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    New-Item -ItemType Directory -Force -Path $staging, $Destination | Out-Null
    Write-Progress -Activity 'Starting Ostraka' -Status "Unpacking Ostraka $Version" -PercentComplete 2
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    # .NET Framework's ExtractToDirectory won't overwrite, so it unpacks into an empty folder first
    [System.IO.Compression.ZipFile]::ExtractToDirectory((Join-Path $AppDir 'source.zip'), $staging)
    $tree = Find-SourceRoot $staging
    if (-not $tree) { throw "The bundled source has no docker-compose.yml: reinstall Ostraka." }
    # /MIR purges what the new tree lacks, but only in a copy this app made; /XF and /XD files
    # and folders are neither copied nor purged, and /XJ leaves junctions (and what they point
    # at) alone. Exit codes under 8 are success.
    $marker = Join-Path $Destination '.ostraka-version'
    $mirror = (Test-Path -LiteralPath $marker -PathType Leaf) -and -not (Test-Path -LiteralPath (Join-Path $Destination '.git'))
    $mode = if ($mirror) { '/MIR' } else { '/E' }
    $ErrorActionPreference = 'Continue'
    robocopy $tree $Destination $mode /XJ /XF .env .ostraka-version /XD .git /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    Remove-Item -Recurse -Force $staging
    if ($code -ge 8) { throw "Couldn't copy Ostraka's files into $Destination (robocopy exit $code)." }
    Set-Content -Path (Join-Path $Destination '.ostraka-version') -Value $Version -Encoding ASCII
}

function Initialize-SourceTree {
    <#
    .SYNOPSIS
        Make sure OSTRAKA_HOME holds this version's source: unpack on first run, offer an update after.
    .OUTPUTS
        'fresh' (just unpacked), 'updated', or 'ready'.
    #>
    $paths = Get-AppPaths
    $home_ = $paths.Home
    if (-not $Packaged) { return 'ready' }
    # A git checkout is never overwritten
    if (Test-Path (Join-Path $home_ '.git')) { return 'ready' }
    $version = Get-AppVersion
    $marker = Join-Path $home_ '.ostraka-version'
    if (-not (Test-Path $marker)) {
        if ((Test-Path $home_) -and @(Get-ChildItem -Force $home_).Count) {
            throw "$home_ already exists and isn't an Ostraka folder. Move it, or set OSTRAKA_HOME to another folder."
        }
        Expand-Source $home_ $version
        # For a CLI whose status has no has_admin: ask once the stack is up, and on later launches
        # until it's done
        New-Item -ItemType Directory -Force -Path $paths.State | Out-Null
        Set-Content -Path $paths.AdminPending -Value $home_ -Encoding ASCII
        return 'fresh'
    }
    $installed = (Get-Content $marker -Raw).Trim()
    # An update cut short can leave the marker without the files: unpack again
    if (-not (Test-Path (Join-Path $home_ $CliPath))) {
        Expand-Source $home_ $version
        return 'updated'
    }
    $order = Compare-Version $version $installed
    if ($order -gt 0) {
        Write-Host ''
        Write-Host "This app is Ostraka $version; the copy in $home_ is $installed."
        $answer = Read-Host "Update it to $version? Your incidents, settings and .env stay. [Y/n]"
        if ($answer -notmatch '^[nN]') {
            Expand-Source $home_ $version
            return 'updated'
        }
    } elseif ($order -lt 0) {
        Write-Host "The copy in $home_ ($installed) is newer than this app ($version); it runs as it is." -ForegroundColor Yellow
    }
    return 'ready'
}

function ConvertFrom-ProgressLine {
    <# 'progress <0-100> <message>' as @{ Percent; Message }, or $null for any other line. #>
    param([string]$Line)
    if ($Line -match '^progress\s+(\d{1,3})\s*(.*)$') {
        $percent = [math]::Min(100, [int]$Matches[1])
        $message = if ($Matches[2].Trim()) { $Matches[2].Trim() } else { 'Starting' }
        return @{ Percent = $percent; Message = $message }
    }
    return $null
}

function Invoke-OstrakaCli {
    <#
    .SYNOPSIS
        Run the host CLI in OSTRAKA_HOME; returns @{ Code; Output }.
    .PARAMETER Progress
        Turn its 'progress <0-100> <message>' lines into a console progress bar.
    #>
    param([string[]]$Arguments, [switch]$Progress)
    $paths = Get-AppPaths
    $cli = Join-Path $paths.Home $CliPath
    if (-not (Test-Path $cli)) { throw "$($paths.Home) has no $CliPath`: is it an Ostraka source tree?" }
    $env:OSTRAKA_HOME = $paths.Home
    # The same PowerShell this runs in, so the CLI behaves as it does here (5.1 from the shortcuts)
    $shell = (Get-Process -Id $PID).Path
    $output = New-Object System.Collections.Generic.List[string]
    $ErrorActionPreference = 'Continue'
    Push-Location $paths.Home
    try {
        & $shell -NoProfile -ExecutionPolicy Bypass -File $cli @Arguments 2>&1 | ForEach-Object {
            $line = "$_"
            $step = ConvertFrom-ProgressLine $line
            if ($Progress -and $step) {
                Write-Progress -Activity 'Starting Ostraka' -Status $step.Message -PercentComplete $step.Percent
            } else {
                $output.Add($line)
                Write-Verbose $line
            }
        }
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($Progress) { Write-Progress -Activity 'Starting Ostraka' -Completed }
    return @{ Code = $code; Output = @($output) }
}

function Get-LocalUrl {
    <# The stack's URL, from 'ostraka url' (it knows OSTRAKA_PORT). #>
    $result = Invoke-OstrakaCli @('url')
    $url = $result.Output | Where-Object { $_ -match '^https?://' } | Select-Object -Last 1
    if ($result.Code -eq 0 -and $url) { return $url.Trim().TrimEnd('/') }
    $port = if ($env:OSTRAKA_PORT) { $env:OSTRAKA_PORT } else { '24747' }
    return "http://127.0.0.1:$port"
}

function Test-LocalHealth {
    <# Whether the stack already answers GET /api/health with status ok. #>
    param([string]$Url)
    try {
        $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 3 -UseBasicParsing
        return [bool](($health -is [System.Management.Automation.PSCustomObject]) -and
                      $health.PSObject.Properties['status'] -and $health.status -eq 'ok')
    } catch {
        return $false
    }
}

function Test-NeedsFirstAdmin {
    <#
    .SYNOPSIS
        Whether to ask for the first admin: 'status --json' says has_admin; when it doesn't (no
        such field, or null while the stack is down), the marker a fresh unpack left does.
    #>
    $paths = Get-AppPaths
    $result = Invoke-OstrakaCli @('status', '--json')
    try {
        $status = ($result.Output -join "`n") | ConvertFrom-Json
        if ($status.PSObject.Properties['has_admin'] -and $null -ne $status.has_admin) {
            if ($status.has_admin) { Remove-Item -Force $paths.AdminPending -ErrorAction SilentlyContinue }
            return -not $status.has_admin
        }
    } catch {
        Write-Verbose "status --json: $($_.Exception.Message)"
    }
    return Test-Path $paths.AdminPending
}

function Read-FirstAdmin {
    <# On a fresh install: the first admin's email, then their invitation link (or $null). #>
    $paths = Get-AppPaths
    Write-Host ''
    Write-Host 'Ostraka is running. Who is its first admin? They set a password and an authenticator app next.'
    while ($true) {
        $email = (Read-Host 'Email (blank to do this later)').Trim()
        if (-not $email) {
            Write-Host 'Ostraka asks again next time it opens.'
            return $null
        }
        if ($email -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
            Write-Host 'That isn''t an email address.' -ForegroundColor Yellow
            continue
        }
        $result = Invoke-OstrakaCli @('first-admin', $email)
        $link = $result.Output | Where-Object { $_ -match '^https?://\S+$' } | Select-Object -Last 1
        if ($result.Code -eq 0 -and $link) {
            Remove-Item -Force $paths.AdminPending -ErrorAction SilentlyContinue
            return $link.Trim()
        }
        if ($result.Code -eq $ExitHasAdmin) {
            # It refuses once there's an admin: someone set one up already
            Remove-Item -Force $paths.AdminPending -ErrorAction SilentlyContinue
            Write-Host 'Ostraka has an admin already: sign in as them.'
            return $null
        }
        # Anything else (the database not ready, say) settles nothing: try again now, or next launch
        Write-Host "Couldn't create the first admin's invitation (exit $($result.Code)):" -ForegroundColor Red
        Write-Host (($result.Output | Select-Object -Last 15) -join "`n")
        Write-Host 'Give the email again to retry, or leave it blank: Ostraka asks again next time it opens.'
    }
}

function Start-Local {
    <# Ostraka on this computer: Docker, the source tree, the stack, then the window. #>
    Wait-Docker
    $tree = Initialize-SourceTree
    $url = Get-LocalUrl
    # Already up and nothing new to build: straight to the window
    if ($tree -ne 'ready' -or -not (Test-LocalHealth $url)) {
        Write-Host 'Starting Ostraka on this computer (the first start builds it, which takes a few minutes)...'
        Write-Progress -Activity 'Starting Ostraka' -Status 'Starting' -PercentComplete 5
        $result = Invoke-OstrakaCli @('start', '--progress') -Progress
        if ($result.Code -ne 0) {
            throw "Ostraka didn't start (exit $($result.Code)):`n$(($result.Output | Select-Object -Last 20) -join "`n")"
        }
    }
    if (Test-NeedsFirstAdmin) {
        $link = Read-FirstAdmin
        if ($link) { Open-Window $link; return }
    }
    Open-Window $url
}

function Stop-Local {
    <# Stop Ostraka on this computer; the data stays. #>
    if ((Get-DockerState) -ne 'ok') { Write-Host 'Docker isn''t running, so neither is Ostraka.'; return }
    Write-Host 'Stopping Ostraka...'
    $result = Invoke-OstrakaCli @('stop')
    if ($result.Code -ne 0) { throw "Couldn't stop Ostraka:`n$(($result.Output | Select-Object -Last 15) -join "`n")" }
    Write-Host 'Stopped. Your data stays; open Ostraka to start it again.'
}

# ── Help ──────────────────────────────────────────────────────────────────────

function Show-About {
    Write-Host "Ostraka $(Get-AppVersion)"
    Write-Host 'Incident management and response.'
    Write-Host 'Licensed under the Elastic License 2.0; paid editions under COMMERCIAL-LICENSE.md.'
    Write-Host "Copyright (c) $((Get-Date).Year) cmdaltr."
}

function Open-Licence {
    <# <server>/help/licence, which is public; the files beside the app when no server answers. #>
    param([string]$Server)
    $url = if ($Server -eq 'local') { Get-LocalUrl } else { $Server }
    $answers = if ($Server -eq 'local') { Test-LocalHealth $url }
               elseif ($url) { (Test-OstrakaServer $url -TimeoutSec 5).Ok }
               else { $false }
    if ($answers) { Open-Window "$url/help/licence"; return }
    $dir = if ($Packaged) { $AppDir } else { (Get-AppPaths).Home }
    foreach ($name in 'LICENSE', 'COMMERCIAL-LICENSE.md') {
        $file = Join-Path $dir $name
        if (Test-Path $file) { Start-Process notepad.exe -ArgumentList "`"$file`"" }
    }
}

# ── Main ──────────────────────────────────────────────────────────────────────

function Invoke-Main {
    $Host.UI.RawUI.WindowTitle = 'Ostraka'
    # Windows PowerShell 5.1 on older .NET may not offer TLS 1.2 unless asked
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    if ($About) { Show-About; return }
    $setting = Get-ServerSetting

    if ($ChangeServer) {
        if ($setting.Locked) {
            Write-Host "Your IT team set Ostraka's server ($($setting.Value), through $($setting.Source)): it can't be changed here."
            Read-Host 'Press Enter to close' | Out-Null
            return
        }
        Remove-Item -Force (Get-AppPaths).ServerFile -ErrorAction SilentlyContinue
        if ($setting.Source -eq 'OSTRAKA_SERVER') {
            Write-Host "OSTRAKA_SERVER is set ($env:OSTRAKA_SERVER), and it wins over what you choose here until it's removed." -ForegroundColor Yellow
        }
        $setting = [pscustomobject]@{ Value = ''; Locked = $false; Source = 'none' }
    }

    if ($Licence) { Open-Licence $setting.Value; return }

    if ($Stop) {
        if ($setting.Value -eq 'local') { Stop-Local }
        elseif ($setting.Value) { Write-Host "Ostraka runs on $($setting.Value); its admins start and stop it." }
        else { Write-Host 'Ostraka isn''t set up on this computer yet.' }
        Start-Sleep -Seconds 3
        return
    }

    $server = $setting.Value
    if (-not $server) { $server = Read-FirstRunChoice }
    if ($server -eq 'local') { Start-Local; return }

    while ($true) {
        $check = Test-OstrakaServer $server
        if ($check.Ok) { Open-Window $server; return }
        Write-Host $check.Message -ForegroundColor Yellow
        if ($setting.Locked -or $setting.Source -eq 'OSTRAKA_SERVER') {
            throw "Ostraka's server is set by $($setting.Source). If it stays unreachable, ask your IT team."
        }
        $choices = [System.Management.Automation.Host.ChoiceDescription[]]@(
            (New-Object System.Management.Automation.Host.ChoiceDescription '&Try again', 'Check the server again.'),
            (New-Object System.Management.Automation.Host.ChoiceDescription '&Change server', 'Choose another server, or run Ostraka here.'),
            (New-Object System.Management.Automation.Host.ChoiceDescription '&Quit', 'Close Ostraka.')
        )
        $pick = $Host.UI.PromptForChoice('Ostraka', "Couldn't open $server.", $choices, 0)
        if ($pick -eq 2) { return }
        if ($pick -eq 1) {
            Remove-Item -Force (Get-AppPaths).ServerFile -ErrorAction SilentlyContinue
            $server = Read-FirstRunChoice
            if ($server -eq 'local') { Start-Local; return }
        }
    }
}

# Dot-sourced (the tests), only the functions load
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-Main
    } catch {
        Write-Progress -Activity 'Starting Ostraka' -Completed
        Write-Host ''
        Write-Host "Ostraka: $($_.Exception.Message)" -ForegroundColor Red
        Read-Host 'Press Enter to close' | Out-Null
        exit 1
    }
}
