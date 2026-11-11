<#
.SYNOPSIS
    Set up Ostraka as a team server on Windows Server or Windows 10/11, inside WSL 2.

.DESCRIPTION
    Ostraka's images are Linux containers, so on Windows the server runs in a WSL 2 Ubuntu
    distribution with Docker Engine. This checks WSL and the distribution (and, with -InstallDocker,
    puts Docker Engine in it), then runs deploy/linux/install.sh inside it as root with the same arguments:
    the source tree goes to /opt/ostraka on the distribution's own disk, .env gets random secrets,
    the stack starts and the first admin's invitation link is printed.

    WSL doesn't start a distribution at boot, and stops one nobody is using, so this also registers a
    Scheduled Task ("Ostraka") that runs at startup, whether anyone signs in or not: it starts the
    distribution and keeps it running, and Docker (under systemd) brings the stack back, every
    service being restart: unless-stopped. With -PortProxy the task also points a Windows port proxy
    at the distribution's new address at each boot. -NoService leaves the task out.
    deploy/windows/uninstall-service.ps1 takes it all away again.

    Run it from an Ostraka source tree (a clone or an unpacked release), in an administrator
    PowerShell 5.1 or later (the boot task needs one; with -NoService any prompt will do).

.PARAMETER Url
    The address people open Ostraka at, e.g. https://ostraka.example.org (or just the hostname).

.PARAMETER Admin
    The first admin's email address; their invitation link is printed at the end.

.PARAMETER Distribution
    The WSL distribution to use (default: the first Ubuntu one installed).

.PARAMETER Dir
    Where the source tree goes inside the distribution (default /opt/ostraka).

.PARAMETER Caddy
    acme or internal: run Caddy in the stack for HTTPS on 80 and 443 (see deploy/linux/install.sh --help).

.PARAMETER InstallDocker
    Install Docker Engine from Docker's apt repository inside the distribution, with systemd on,
    when it isn't there already.

.PARAMETER NoStart
    Write the tree and .env without starting the stack.

.PARAMETER NoService
    Don't register the boot task: Ostraka then runs only while WSL happens to be running.

.PARAMETER TaskCredential
    The account the boot task runs as: the one whose WSL holds the distribution (WSL distributions
    belong to a user, so SYSTEM can't start them). Its password is stored with the task, which is what
    lets it run with nobody signed in. Asked for when not given.

.PARAMETER PortProxy
    Windows Server: forward 80 and 443 on every Windows address to the distribution, now and at each
    boot, and allow them through Windows Firewall. Needs -Caddy, which is what listens on them.

.EXAMPLE
    .\deploy\windows\install-service.ps1 -Url ostraka.example.org -Admin you@example.org -Caddy acme -InstallDocker
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string] $Url = '',
    [string] $Admin = '',
    [string] $Distribution = '',
    [string] $Dir = '/opt/ostraka',
    [ValidateSet('', 'acme', 'internal')] [string] $Caddy = '',
    [switch] $InstallDocker,
    [switch] $NoStart,
    [switch] $NoService,
    [pscredential] $TaskCredential,
    [switch] $PortProxy,
    # Anything else goes to deploy/linux/install.sh as it is, so `ostraka server init` can pass its arguments on.
    [Parameter(ValueFromRemainingArguments = $true)] [string[]] $Rest = @()
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Step([string] $Text) { Write-Host "`n==> $Text" }
function Fail([string] $Text) { [Console]::Error.WriteLine("install-service.ps1: $Text"); exit 1 }

if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) { Fail 'This is for Windows. On Linux run deploy/linux/install.sh.' }

$TaskName = 'Ostraka'
$StateDir = Join-Path $env:ProgramData 'Ostraka'
$BootScript = Join-Path $StateDir 'start-server.ps1'
$ProxyPorts = @(80, 443)

# The directory goes into the boot task's script inside quotes, so it's kept to a plain path.
if ($Dir -notmatch '^/[A-Za-z0-9._/-]+$') { Fail "-Dir must be an absolute Linux path of letters, digits, '.', '_', '-' and '/' (got '$Dir')." }
if ($PortProxy -and -not $Caddy) { Fail '-PortProxy forwards 80 and 443 to Caddy, so it needs -Caddy acme or -Caddy internal.' }
if ($PortProxy -and $NoService) { Fail '-PortProxy is redone at each boot by the boot task, so it needs the task: drop -NoService.' }
if (-not $NoService) {
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Fail 'The boot task and port proxy need an elevated PowerShell (Run as administrator), or add -NoService.'
    }
}

$tree = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$setup = Join-Path $PSScriptRoot '..\linux\install.sh'
if (-not (Test-Path (Join-Path $tree 'docker-compose.yml'))) { Fail "$tree isn't an Ostraka source tree." }

# A checkout made with core.autocrlf=true has CRLF line endings, and Bash inside WSL can't run those.
$raw = [IO.File]::ReadAllText($setup)
if ($raw.Contains("`r`n")) {
    Fail ("$setup has Windows line endings, so Bash can't run it (or the tree's other scripts). Clone again " +
        "with: git -c core.autocrlf=false clone https://github.com/cmdaltr/ostraka.git")
}

# wsl.exe writes UTF-16 unless told otherwise; WSL_UTF8 makes its listings readable as text.
$env:WSL_UTF8 = '1'

# Under 'Stop', PowerShell 5.1 turns every stderr line from a native command redirected with 2>&1 into a
# terminating error, so the preference is 'Continue' here and the exit code says how it went.
function Invoke-Wsl([string[]] $Arguments, [string] $InputText = '') {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($InputText) { $out = $InputText | & wsl.exe @Arguments 2>&1 } else { $out = & wsl.exe @Arguments 2>&1 }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return @{ Code = $code; Text = (($out | ForEach-Object { "$_" }) -join "`n") -replace "`0", '' }
}

# ---- WSL ------------------------------------------------------------------------------------------

Step 'WSL 2'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Fail ('WSL is not installed. From an administrator PowerShell: wsl --install --no-distribution, restart, ' +
        'then wsl --install -d Ubuntu-24.04 and run this again. Windows Server 2022 needs its cumulative updates first.')
}
$status = Invoke-Wsl @('--status')
if ($status.Code -ne 0 -and $status.Text -match 'not (installed|enabled)') {
    Fail 'WSL is not enabled yet. From an administrator PowerShell: wsl --install --no-distribution, then restart.'
}

$list = Invoke-Wsl @('--list', '--verbose')
$distros = @()
foreach ($line in ($list.Text -split "`n")) {
    # "* Ubuntu-24.04    Running   2"  (the * marks the default)
    if ($line -match '^\s*\*?\s*(\S+)\s+(Running|Stopped|Installing|Converting)\s+(\d)\s*$') {
        $distros += [pscustomobject]@{ Name = $Matches[1]; Version = [int]$Matches[3] }
    }
}
if (-not $Distribution) {
    $ubuntu = @($distros | Where-Object { $_.Name -like 'Ubuntu*' })
    if ($ubuntu.Count -eq 0) {
        Fail 'No Ubuntu distribution in WSL. Install one with: wsl --install -d Ubuntu-24.04 (set its user up when it opens), then run this again.'
    }
    $Distribution = $ubuntu[0].Name
}
$distro = @($distros | Where-Object { $_.Name -eq $Distribution })
if ($distro.Count -eq 0) { Fail "WSL has no distribution called $Distribution (wsl --list --verbose)." }
if ($distro[0].Version -ne 2) {
    Fail "$Distribution runs under WSL 1, which can't run Docker. Convert it: wsl --set-version $Distribution 2"
}
Write-Host "Using $Distribution (WSL 2)."

# ---- Docker inside it -----------------------------------------------------------------------------

Step "Docker Engine in $Distribution"
$docker = Invoke-Wsl @('-d', $Distribution, '-u', 'root', '--', 'sh', '-c', 'docker compose version && docker info --format ok')
if ($docker.Code -ne 0) {
    if (-not $InstallDocker) {
        Fail ("Docker isn't working inside $Distribution. Run this again with -InstallDocker to install Docker Engine " +
            "there, or on Windows 10/11 turn on Docker Desktop's WSL integration for $Distribution. (Docker Desktop " +
            "isn't licensed for Windows Server.)")
    }
    # Docker's own apt repository, and systemd so dockerd starts with the distribution.
    # The script goes in on stdin (bash -s), not as an argument: PowerShell 5.1 doesn't escape the
    # double quotes in an argument to a native command, so bash would get it cut short. It's one
    # function, read whole before it runs, with stdin from /dev/null so apt can't eat the rest; the
    # closing comment soaks up the CR of the CRLF PowerShell adds after piped text.
    $install = @'
install_docker() {
  set -e
  . /etc/os-release
  if ! grep -q '^systemd=true' /etc/wsl.conf 2>/dev/null; then
    printf '[boot]\nsystemd=true\n' >> /etc/wsl.conf
    echo restart-needed > /tmp/ostraka-wsl-restart
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl gnupg >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
}
install_docker </dev/null
'@
    # No newline after the comment: PowerShell adds CRLF after piped text, and a line of just CR is a
    # command bash can't find.
    $script = ($install -replace "`r", '') + "`n# end"
    $result = Invoke-Wsl @('-d', $Distribution, '-u', 'root', '--', 'bash', '-s') $script
    if ($result.Code -ne 0) { Write-Host $result.Text; Fail "Installing Docker in $Distribution failed." }
    $restart = Invoke-Wsl @('-d', $Distribution, '-u', 'root', '--', 'sh', '-c', 'test -e /tmp/ostraka-wsl-restart')
    if ($restart.Code -eq 0) {
        Write-Host "Restarting $Distribution with systemd on."
        & wsl.exe --terminate $Distribution | Out-Null
    }
    Invoke-Wsl @('-d', $Distribution, '-u', 'root', '--', 'sh', '-c', 'systemctl enable --now docker >/dev/null 2>&1 || service docker start') | Out-Null
    $docker = Invoke-Wsl @('-d', $Distribution, '-u', 'root', '--', 'sh', '-c', 'docker compose version && docker info --format ok')
    if ($docker.Code -ne 0) { Write-Host $docker.Text; Fail "Docker still isn't answering in $Distribution." }
}
Write-Host ($docker.Text -split "`n")[0]

# ---- deploy/linux/install.sh inside it ------------------------------------------------------------

$wslTree = (Invoke-Wsl @('-d', $Distribution, '--', 'wslpath', '-a', $tree)).Text.Trim()
if (-not $wslTree.StartsWith('/')) { Fail "Couldn't find $tree from inside $Distribution." }

$arguments = @('--source', $wslTree, '--dir', $Dir)
if ($Url) { $arguments += @('--url', $Url) }
if ($Admin) { $arguments += @('--admin', $Admin) }
if ($Caddy) { $arguments += @('--caddy', $Caddy) }
if ($NoStart) { $arguments += '--no-start' }
$arguments += $Rest

Step "deploy/linux/install.sh in $Distribution"
# As root: /opt needs it, and so does Docker for an account that isn't in the docker group.
& wsl.exe -d $Distribution -u root -- bash "$wslTree/deploy/linux/install.sh" @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# ---- the boot task --------------------------------------------------------------------------------

# Run by the task at startup. WSL starts no distribution at boot and stops one nobody is using, so it
# starts the distribution and stays attached to it (sleep infinity); Docker, under systemd, brings the
# stack back by its restart policy, and `docker compose up -d` covers anything that was never started.
$bootTemplate = @'
# Written by deploy/windows/install-service.ps1 and run at startup by the Ostraka Scheduled Task.
# deploy/windows/uninstall-service.ps1 removes it.
param([switch] $ProxyOnly)
$ErrorActionPreference = 'Continue'
$env:WSL_UTF8 = '1'
$Distribution = '@DISTRIBUTION@'
$ProxyPorts = @(@PORTS@)

# WSL 2 gets a new address at each boot, so the port proxy is pointed at it again.
if ($ProxyPorts.Count -gt 0) {
    $ip = ''
    for ($i = 0; $i -lt 60 -and -not $ip; $i++) {
        $ip = ((& wsl.exe -d $Distribution -u root -- hostname -I 2>$null) -join ' ').Trim().Split(' ')[0]
        if (-not $ip) { Start-Sleep -Seconds 2 }
    }
    if ($ip) {
        foreach ($port in $ProxyPorts) {
            & netsh.exe interface portproxy delete v4tov4 listenport=$port listenaddress=0.0.0.0 2>$null | Out-Null
            & netsh.exe interface portproxy add v4tov4 listenport=$port listenaddress=0.0.0.0 connectport=$port connectaddress=$ip | Out-Null
        }
    }
}
if ($ProxyOnly) { exit 0 }

$start = @"
start_ostraka() {
  docker info >/dev/null 2>&1 || systemctl start docker >/dev/null 2>&1 || service docker start >/dev/null 2>&1 || true
  i=0
  until docker info >/dev/null 2>&1 || [ "`$i" -ge 90 ]; do sleep 2; i=`$((i + 1)); done
  cd '@DIR@' && docker compose up -d >/dev/null 2>&1
}
start_ostraka </dev/null
exec sleep infinity
# end
"@
# The closing comment takes the CR of the CRLF that PowerShell adds after piped text.
($start -replace "`r", '') | & wsl.exe -d $Distribution -u root -- bash -s
'@

if ($NoService) {
    Step 'No boot task (-NoService)'
    Write-Host "WSL stops an idle distribution, so Ostraka runs only while $Distribution does. To keep it up, run"
    Write-Host 'this again without -NoService, or keep a session open with:'
    Write-Host "    wsl -d $Distribution -u root -- sh -c `"cd $Dir && docker compose up -d && exec sleep infinity`""
} else {
    Step "Scheduled Task '$TaskName' (at startup)"
    $ports = if ($PortProxy) { $ProxyPorts -join ', ' } else { '' }
    $boot = $bootTemplate.Replace('@DISTRIBUTION@', $Distribution).Replace('@DIR@', $Dir).Replace('@PORTS@', $ports)

    # Only SYSTEM and Administrators may change what the task runs with the highest privileges.
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    & icacls.exe $StateDir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't restrict $StateDir to administrators." }
    [IO.File]::WriteAllText($BootScript, $boot)
    $state = [ordered]@{ Distribution = $Distribution; Dir = $Dir; PortProxy = [bool]$PortProxy; Ports = @($ProxyPorts) }
    [IO.File]::WriteAllText((Join-Path $StateDir 'server.json'), ($state | ConvertTo-Json))

    # WSL distributions belong to a user, so the task runs as the one this distribution belongs to, with
    # the password stored so it runs with nobody signed in.
    if (-not $TaskCredential) {
        $TaskCredential = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" `
            -Message "Ostraka's boot task runs as the account that owns $Distribution in WSL. Its password is stored with the task."
        if (-not $TaskCredential) { Fail 'No account for the boot task. Run again with -TaskCredential, or -NoService.' }
    }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$BootScript`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT30S'
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -User $TaskCredential.UserName -Password $TaskCredential.GetNetworkCredential().Password `
            -RunLevel Highest -Force `
            -Description "Starts $Distribution at boot and keeps it running, so Docker brings Ostraka back." | Out-Null
    } catch {
        Fail "Couldn't register the boot task as $($TaskCredential.UserName): $($_.Exception.Message)"
    }
    Write-Host "Registered: at each startup it starts $Distribution as $($TaskCredential.UserName) and keeps it running."

    if ($PortProxy) {
        Remove-NetFirewallRule -Name 'Ostraka-HTTPS' -ErrorAction SilentlyContinue
        New-NetFirewallRule -Name 'Ostraka-HTTPS' -DisplayName 'Ostraka (HTTP and HTTPS)' -Direction Inbound `
            -Protocol TCP -LocalPort $ProxyPorts -Action Allow | Out-Null
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $BootScript -ProxyOnly
        Write-Host "Port proxy: $($ProxyPorts -join ' and ') on every Windows address go to $Distribution, and are open in Windows Firewall."
    }
    # Now as well as at the next boot: without it, WSL stops the distribution soon after this exits.
    if (-not $NoStart) { Start-ScheduledTask -TaskName $TaskName }
}

# ---- reaching it ----------------------------------------------------------------------------------

Step 'Reaching it from other machines'
if ($PortProxy) {
    Write-Host "Other machines reach Caddy on 80 and 443 through the port proxy, which the boot task redoes at each startup."
} else {
    $ports = if ($Caddy) { '80,443' } else { '24747' }
    Write-Host @"
On this machine Ostraka answers at http://127.0.0.1:24747 (WSL forwards its loopback to Windows).
WSL 2 sits behind its own NAT with a new address at each boot, so other machines need one of:

  * A reverse proxy on Windows (IIS with Application Request Routing, or Caddy for Windows) serving
    HTTPS and passing requests to http://127.0.0.1:24747, as in deploy\README.md.

  * On Windows 11 22H2 or later, mirrored networking, so WSL's ports are Windows' ports:
    in %UserProfile%\.wslconfig put
        [wsl2]
        networkingMode=mirrored
    then wsl --shutdown, and open the port: New-NetFirewallRule -DisplayName Ostraka -Direction Inbound -Protocol TCP -LocalPort $ports -Action Allow

  * On Windows Server with Caddy (-Caddy acme or internal), run this again with -PortProxy: 80 and 443
    are forwarded to the distribution, and the boot task redoes it at each startup.
"@
}
