<#
.SYNOPSIS
    Drives the Ostraka stack from a Windows host: the same commands as ./ostraka (desktop/README.md).
    Not ostraka.ps1: Windows and macOS filesystems ignore case, and Ostraka.ps1 is the launcher.

.DESCRIPTION
    ostraka-cli.ps1 start [--progress] | stop | status [--json] | url | first-admin EMAIL | logs [service]
                | server init [options] | app install | app uninstall | version | help
    Anything else goes to the backend's own CLI (docker compose exec backend ostraka ...).

    Written for Windows PowerShell 5.1, the one every Windows 10 and 11 has: no ternaries, no
    `??`, no `&&`. Arguments are read from $args rather than param() so that `--progress` and
    `--json` arrive as words, the same as on macOS and Linux.

    Exit codes: 0 running or done, 1 failed, 2 usage, 3 stopped, 4 Docker missing or not running,
    5 first-admin when there's an admin already.
#>

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version 2.0

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
# Set, so `exit $LASTEXITCODE` works under StrictMode even when no native command has run
$global:LASTEXITCODE = 0
$ExitStopped = 3
$ExitNoDocker = 4
$ExitAdminExists = 5  # first-admin: the backend's bootstrap-admin says so, and it's passed on as it is
# Compose names a project after its folder, so an unpacked %USERPROFILE%\Ostraka and a checkout
# called ostraka would share volumes. An unpacked tree (it has .ostraka-version) is project ostraka-app.
$UnpackedProject = 'ostraka-app'
$script:Progress = $false

function Show-Usage {
    $url = Get-LocalUrl
    @"
Usage: ostraka <command> [options]

  start [--progress]        Start Ostraka (writes .env with random secrets the first time)
  stop                      Stop it; the data stays
  status [--json]           Exit 0 running, 3 stopped, 4 Docker missing or not running
  url                       The local address, $url
  first-admin EMAIL         On a fresh install: the first admin's invitation link
                            (exit $ExitAdminExists when there's an admin already)
  logs [service]            Follow the logs
  server init [options]     A central server in WSL 2 (deploy\windows\install-service.ps1)
  app install | uninstall   Start menu and desktop shortcuts for this source tree
  version                   The version of this source tree
  help                      This list

Anything else goes to the backend's own CLI, for example:
  ostraka licence install FILE --accept-terms "Name <email>"
  ostraka import --sample
"@
}

# -- Output --------------------------------------------------------------------
# With --progress, stdout carries only `progress N message` lines for the launcher to parse, so
# everything meant for a person goes to stderr instead
function Say([string]$Message) {
    if ($script:Progress) { [Console]::Error.WriteLine($Message) } else { Write-Output $Message }
}

function Send-Progress([int]$Percent, [string]$Message) {
    if ($script:Progress) { [Console]::Out.WriteLine("progress $Percent $Message") }
}

function Stop-WithError([int]$Code, [string]$Message) {
    [Console]::Error.WriteLine("Error: $Message")
    exit $Code
}

# -- Settings ------------------------------------------------------------------
function Get-EnvValue([string]$Name) {
    $file = Join-Path $Root '.env'
    if (-not (Test-Path -LiteralPath $file)) { return '' }
    $value = ''
    foreach ($line in [IO.File]::ReadAllLines($file)) {
        if ($line.StartsWith("$Name=")) { $value = $line.Substring($Name.Length + 1) }
    }
    return $value
}

function Get-Port {
    $port = $env:OSTRAKA_PORT
    if (-not $port) { $port = Get-EnvValue 'OSTRAKA_PORT' }
    if (-not $port) { $port = '24747' }
    return $port
}

function Get-LocalUrl { return "http://127.0.0.1:$(Get-Port)" }

function Get-TreeVersion {
    $init = Join-Path $Root 'backend\ostraka\__init__.py'
    if (-not (Test-Path -LiteralPath $init)) { return '' }
    foreach ($line in [IO.File]::ReadAllLines($init)) {
        if ($line -match '^__version__ = "(.*)"') { return $Matches[1] }
    }
    return ''
}

function New-RandomHex([int]$Bytes) {
    $buffer = New-Object byte[] $Bytes
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($buffer) } finally { $rng.Dispose() }
    return (($buffer | ForEach-Object { $_.ToString('x2') }) -join '')
}

# As `make env`, but a local install's links point at this computer on its own port
# (OSTRAKA_PORT). An existing .env is never touched: it holds the secrets the
# data volumes were created with.
function Write-EnvFile {
    $envFile = Join-Path $Root '.env'
    if (Test-Path -LiteralPath $envFile) { return }
    $example = Join-Path $Root '.env.example'
    if (-not (Test-Path -LiteralPath $example)) { Stop-WithError 1 "there's no .env or .env.example in $Root" }
    $url = Get-LocalUrl
    $lines = foreach ($line in [IO.File]::ReadAllLines($example)) {
        if ($line -eq 'POSTGRES_PASSWORD=') { "POSTGRES_PASSWORD=$(New-RandomHex 24)" }
        elseif ($line -eq 'OSTRAKA_SECRET_KEY=') { "OSTRAKA_SECRET_KEY=$(New-RandomHex 32)" }
        elseif ($line.StartsWith('OSTRAKA_PUBLIC_URL=')) { "OSTRAKA_PUBLIC_URL=$url" }
        else { $line }
    }
    $lines = @($lines)
    if ((Test-Unpacked) -and -not ($lines | Where-Object { $_.StartsWith('COMPOSE_PROJECT_NAME=') })) {
        $lines += @('', "COMPOSE_PROJECT_NAME=$UnpackedProject")
    }
    # LF and no BOM: Compose reads this file too, and PowerShell 5.1's UTF8 encoding adds a BOM
    $text = (($lines -join "`n") + "`n")
    [IO.File]::WriteAllText($envFile, $text, (New-Object Text.UTF8Encoding($false)))
    Protect-EnvFile $envFile
    Say "Wrote $envFile with new secrets."
}

# Only this account may read the secrets (the Windows counterpart of chmod 600). By SID, as a name
# can be ambiguous (a domain and a local account) or not resolve at all. A file left readable by
# others is removed: nothing has used its secrets yet, and the next start writes another.
function Protect-EnvFile([string]$File) {
    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        & chmod 600 $File
    } else {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        & icacls.exe $File /inheritance:r /grant:r "*$($sid):(R,W)" *> $null
    }
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -LiteralPath $File -Force -ErrorAction SilentlyContinue
        Stop-WithError 1 "couldn't make $File readable by this account alone (exit $LASTEXITCODE), so it was removed."
    }
}

function Test-Unpacked { return (Test-Path -LiteralPath (Join-Path $Root '.ostraka-version')) }

# .env is where the project name lives, so plain `docker compose` and the uninstallers see the same
# project as this script. An unpacked tree's .env that predates the line gets it; a checkout's .env
# is left alone, and so is a name .env already has (a server's setup writes ostraka). The name is
# set in the environment only when neither it nor .env has one: the environment beats .env in
# compose, so setting it would override a server's own choice.
function Set-ComposeProject {
    if (-not (Test-Unpacked)) { return }
    if (Get-EnvValue 'COMPOSE_PROJECT_NAME') { return }
    $envFile = Join-Path $Root '.env'
    if (Test-Path -LiteralPath $envFile) {
        try {
            $text = [IO.File]::ReadAllText($envFile)
            $add = "COMPOSE_PROJECT_NAME=$UnpackedProject`n"
            # A file that doesn't end in a newline would join the new line to its last one
            if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $add = "`n" + $add }
            [IO.File]::AppendAllText($envFile, $add, (New-Object Text.UTF8Encoding($false)))
        } catch {
            [Console]::Error.WriteLine("Couldn't add the project name to ${envFile}: $($_.Exception.Message)")
        }
        if (Get-EnvValue 'COMPOSE_PROJECT_NAME') { return }
    }
    if (-not $env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME = $UnpackedProject }
}

# -- Docker --------------------------------------------------------------------
function Get-DockerState {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return 'missing' }
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { return 'stopped' }
    return 'ok'
}

# Docker Desktop, started in the background and waited for
function Start-DockerEngine {
    & docker desktop start --detach *> $null
    if ($LASTEXITCODE -ne 0) {
        $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (-not (Test-Path -LiteralPath $exe)) { return $false }
        Start-Process -FilePath $exe -WindowStyle Minimized | Out-Null
    }
    $limit = 120
    if ($env:OSTRAKA_DOCKER_WAIT) { $limit = [int]$env:OSTRAKA_DOCKER_WAIT }
    for ($i = 0; $i -lt $limit; $i++) {
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Assert-Docker([switch]$Wake) {
    $state = Get-DockerState
    if ($state -eq 'missing') {
        [Console]::Error.WriteLine("Error: Docker isn't installed. Ostraka runs in Docker on this computer.")
        [Console]::Error.WriteLine('  Install Docker Desktop: https://www.docker.com/products/docker-desktop/')
        exit $ExitNoDocker
    }
    if ($state -eq 'stopped') {
        $started = $false
        if ($Wake) {
            Send-Progress 2 'Starting Docker'
            Say "Docker isn't running; starting it..."
            $started = Start-DockerEngine
        }
        if (-not $started) { Stop-WithError $ExitNoDocker 'Docker is installed but not running. Start it and try again.' }
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError $ExitNoDocker "Docker's compose plugin is missing (https://docs.docker.com/compose/install/)."
    }
}

# Compose finds its project from the working directory; the caller's is restored afterwards
function Invoke-Compose {
    Set-ComposeProject
    Push-Location -LiteralPath $Root
    try { & docker compose @args } finally { Pop-Location }
}

# A function doesn't pass its pipeline on to a native command, so text for compose's stdin goes
# through here rather than Invoke-Compose (which must leave stdin alone for interactive exec).
# $args only: a named parameter would swallow compose's own -T by prefix.
function Send-ToCompose {
    $text = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    Set-ComposeProject
    Push-Location -LiteralPath $Root
    try { $text | & docker compose @rest } finally { Pop-Location }
}

function Test-BackendRunning {
    $id = Invoke-Compose ps -q --status running backend 2> $null
    return [bool]($id | Where-Object { $_ })
}

# -- Health --------------------------------------------------------------------
$script:HealthVersion = ''

function Test-Healthy {
    $script:HealthVersion = ''
    try {
        $answer = Invoke-RestMethod -Uri "$(Get-LocalUrl)/api/health" -TimeoutSec 3 -UseBasicParsing
    } catch {
        return $false
    }
    # Something else on the port can answer 200 with HTML or plain text, which arrives as a string:
    # only an object with "status": "ok" is Ostraka
    if ($null -eq $answer -or $answer -is [string] -or $answer -is [array]) { return $false }
    if (-not $answer.PSObject.Properties['status'] -or [string]$answer.status -ne 'ok') { return $false }
    if ($answer.PSObject.Properties['version']) { $script:HealthVersion = [string]$answer.version }
    return $true
}

# -- Commands ------------------------------------------------------------------
function Invoke-Start([string[]]$Options) {
    foreach ($option in $Options) {
        if ($option -eq '--progress') { $script:Progress = $true }
        else { Stop-WithError 2 "unknown option for start: $option" }
    }
    Send-Progress 0 'Checking Docker'
    Assert-Docker -Wake
    Send-Progress 5 'Preparing settings'
    Write-EnvFile
    Send-Progress 10 'Building and starting the containers'
    Say 'Starting Ostraka (the first start builds the images, which takes a few minutes)...'

    # Compose runs as its own process so progress can be reported while it builds; its output is
    # passed on to stderr afterwards, as under --progress it would break the line format
    Set-ComposeProject  # Start-Process passes this process's environment on
    $log = [IO.Path]::GetTempFileName()
    $err = [IO.Path]::GetTempFileName()
    $process = Start-Process -FilePath 'docker' -ArgumentList 'compose', 'up', '-d', '--build' `
        -WorkingDirectory $Root -NoNewWindow -PassThru -RedirectStandardOutput $log -RedirectStandardError $err
    $null = $process.Handle  # without this, ExitCode is empty once the process has gone
    $percent = 10
    $ticks = 0
    while (-not $process.HasExited) {
        Start-Sleep -Seconds 1
        $ticks++
        # Creeps towards 70 without reaching it: a build's length can't be known in advance
        if ($percent -lt 69 -and ($ticks % 3) -eq 0) {
            $percent++
            Send-Progress $percent 'Building and starting the containers'
        }
    }
    $process.WaitForExit()
    foreach ($file in @($log, $err)) {
        foreach ($line in [IO.File]::ReadAllLines($file)) { [Console]::Error.WriteLine($line) }
        Remove-Item -LiteralPath $file -ErrorAction SilentlyContinue
    }
    if ($process.ExitCode -ne 0) {
        Stop-WithError 1 "docker compose up failed (exit $($process.ExitCode)); see the output above, or 'ostraka logs'."
    }

    Send-Progress 75 'Waiting for Ostraka to answer'
    $limit = 180
    if ($env:OSTRAKA_START_TIMEOUT) { $limit = [int]$env:OSTRAKA_START_TIMEOUT }
    $waited = 0
    $percent = 75
    while (-not (Test-Healthy)) {
        if ($waited -ge $limit) {
            Stop-WithError 1 "Ostraka didn't answer at $(Get-LocalUrl) within ${limit}s; see 'ostraka logs'."
        }
        Start-Sleep -Seconds 1
        $waited++
        if ($percent -lt 95 -and ($waited % 3) -eq 0) {
            $percent++
            Send-Progress $percent 'Waiting for Ostraka to answer'
        }
    }
    Send-Progress 100 'Ready'
    Say "Ostraka is running at $(Get-LocalUrl)"
    exit 0
}

function Invoke-Status([string[]]$Options) {
    $json = $false
    foreach ($option in $Options) {
        if ($option -eq '--json') { $json = $true }
        else { Stop-WithError 2 "unknown option for status: $option" }
    }
    $state = Get-DockerState
    $running = $false
    if ($state -eq 'ok') { $running = Test-Healthy }
    $version = $script:HealthVersion
    if (-not $version) { $version = Get-TreeVersion }
    # Asked of the backend only while it's up; null otherwise, as there's no one to ask
    $hasAdmin = 'null'
    if ($running) {
        $answer = Invoke-Compose exec -T backend ostraka has-admin 2> $null
        if ($LASTEXITCODE -eq 0) {
            $answer = ([string](@($answer) | Select-Object -Last 1)).Trim()
            if (@('true', 'false') -contains $answer) { $hasAdmin = $answer }
        }
    }
    if ($json) {
        # Built by hand rather than ConvertTo-Json, whose layout differs between 5.1 and 7
        $flag = 'false'
        if ($running) { $flag = 'true' }
        $fields = '{{"running": {0}, "url": "{1}", "version": "{2}", "docker": "{3}", "has_admin": {4}}}'
        Write-Output ($fields -f $flag, (Get-LocalUrl), $version, $state, $hasAdmin)
    } elseif ($state -eq 'missing') {
        Write-Output "Docker isn't installed."
    } elseif ($state -eq 'stopped') {
        Write-Output "Docker isn't running."
    } elseif ($running) {
        Write-Output "Ostraka $version is running at $(Get-LocalUrl)"
    } else {
        Write-Output 'Ostraka is stopped.'
    }
    if ($state -ne 'ok') { exit $ExitNoDocker }
    if (-not $running) { exit $ExitStopped }
    exit 0
}

function Invoke-FirstAdmin([string[]]$Rest) {
    if ($Rest.Count -ne 1) { Stop-WithError 2 'usage: ostraka first-admin EMAIL' }
    Assert-Docker
    if (-not (Test-BackendRunning)) { Stop-WithError $ExitStopped "Ostraka isn't running: ostraka start" }
    $out = Invoke-Compose exec -T backend ostraka bootstrap-admin --link-only $Rest[0]
    # The backend's own exit is passed on: $ExitAdminExists when there's an admin already, 1 otherwise
    $code = $LASTEXITCODE
    if ($code -ne 0) { exit $code }
    $link = @($out | Where-Object { $_ -match '/invite/' }) | Select-Object -Last 1
    if (-not $link) { Stop-WithError 1 "the backend didn't print an invitation link." }
    # The link is opened on this computer, so it gets this computer's address whatever
    # OSTRAKA_PUBLIC_URL says (an older .env may still name another server)
    $token = $link.Substring($link.LastIndexOf('/invite/') + 8).Trim()
    Write-Output "$(Get-LocalUrl)/invite/$token"
    exit 0
}

function Invoke-App([string[]]$Rest) {
    if ($Rest.Count -lt 1 -or @('install', 'uninstall') -notcontains $Rest[0]) {
        Stop-WithError 2 'usage: ostraka app install | uninstall'
    }
    $more = @()
    if ($Rest.Count -gt 1) { $more = $Rest[1..($Rest.Count - 1)] }
    if ($Rest[0] -eq 'uninstall') { $more = @('-Uninstall') + $more }
    Invoke-Part 'desktop\windows\install.ps1' 'The Windows shortcuts' $more
}

function Invoke-Server([string[]]$Rest) {
    if ($Rest.Count -lt 1 -or $Rest[0] -ne 'init') { Stop-WithError 2 'usage: ostraka server init [options]' }
    $more = @()
    if ($Rest.Count -gt 1) { $more = $Rest[1..($Rest.Count - 1)] }
    Invoke-Part 'deploy\windows\install-service.ps1' 'Server setup' $more
}

# Runs another part of phase 3b and exits with its code. A script that ends without `exit` or a
# native command leaves $LASTEXITCODE as it was, so it's reset first and $? covers a failure.
function Invoke-Part([string]$Part, [string]$What, [string[]]$Arguments) {
    $path = Join-Path $Root $Part
    if (-not (Test-Path -LiteralPath $path)) { Stop-WithError 1 "$What isn't in this source tree ($Part is missing)." }
    # Splatting an array of words passes "-Url" as a string, not a parameter name, so the words
    # that name the script's own parameters go in a hashtable; the rest (--hostname, for
    # install.sh) stay words, for its remaining arguments
    $known = (Get-Command -Name $path).Parameters
    $named = @{}
    $words = New-Object System.Collections.ArrayList
    $i = 0
    while ($i -lt $Arguments.Count) {
        $word = $Arguments[$i]
        $i++
        if ($word -match '^-([A-Za-z][A-Za-z0-9]*)(:(.*))?$' -and $known.ContainsKey($Matches[1])) {
            $name = $Matches[1]
            $inline = $Matches[3]
            if ($known[$name].SwitchParameter) {
                $named[$name] = ($null -eq $inline -or @('false', '$false', '0') -notcontains $inline)
            } elseif ($null -ne $inline) {
                $named[$name] = $inline
            } elseif ($i -lt $Arguments.Count) {
                $named[$name] = $Arguments[$i]
                $i++
            } else {
                Stop-WithError 2 "$word needs a value"
            }
        } else {
            [void]$words.Add($word)
        }
    }
    [string[]]$positional = $words.ToArray()
    $global:LASTEXITCODE = 0
    & $path @named @positional
    $ok = $?
    $code = $global:LASTEXITCODE
    if ($code -eq 0 -and -not $ok) { $code = 1 }
    exit $code
}

# The backend's CLI runs in its container, which can't see this computer's files: a file named
# on the command line (`licence install FILE`, `import FILE`) is copied in first and its path
# swapped for the copy's. docker cp would leave the copy root's, unreadable by the backend's
# user; written through its own shell, it belongs to that user.
function Invoke-Backend([string[]]$Words) {
    Assert-Docker
    if (-not (Test-BackendRunning)) { Stop-WithError $ExitStopped "Ostraka isn't running: ostraka start" }
    $passed = @()
    $copies = @()
    $n = 0
    foreach ($word in $Words) {
        if ($word -and (Test-Path -LiteralPath $word -PathType Leaf)) {
            $n++
            $name = [IO.Path]::GetFileName($word) -replace '[^A-Za-z0-9._-]', '_'
            $copy = "/tmp/ostraka-host-$PID-$n-$name"
            $content = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $word).Path))
            # Base64 through stdin: PowerShell 5.1 pipes text, and would change a file's line endings
            Send-ToCompose $content exec -T backend sh -c 'umask 077 && base64 -d > "$1"' sh $copy
            if ($LASTEXITCODE -ne 0) { Stop-WithError 1 "couldn't copy $word into the backend container." }
            $copies += $copy
            $passed += $copy
        } else {
            $passed += $word
        }
    }
    # No terminal for compose to attach when either end is redirected: a launcher, a script, a pipe
    if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
        Invoke-Compose exec -T backend ostraka @passed
    } else {
        Invoke-Compose exec backend ostraka @passed
    }
    $code = $LASTEXITCODE
    if ($copies.Count -gt 0) { Invoke-Compose exec -T backend rm -f @copies *> $null }
    exit $code
}

# -- Dispatch ------------------------------------------------------------------
$command = ''
$rest = @()
if ($args.Count -gt 0) { $command = [string]$args[0] }
if ($args.Count -gt 1) { $rest = [string[]]$args[1..($args.Count - 1)] }

switch -Exact ($command) {
    { @('', 'help', '-h', '--help') -contains $_ } { Show-Usage; exit 0 }
    'start' { Invoke-Start $rest }
    'stop' { Assert-Docker; Invoke-Compose stop; exit $LASTEXITCODE }
    'status' { Invoke-Status $rest }
    'url' { Write-Output (Get-LocalUrl); exit 0 }
    { @('version', '--version') -contains $_ } { Write-Output (Get-TreeVersion); exit 0 }
    'first-admin' { Invoke-FirstAdmin $rest }
    'logs' { Assert-Docker; Invoke-Compose logs -f @rest; exit $LASTEXITCODE }
    'server' { Invoke-Server $rest }
    'app' { Invoke-App $rest }
    default { Invoke-Backend (@($command) + $rest) }
}
