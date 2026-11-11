<#
.SYNOPSIS
    ost: the Ostraka responder client for Windows (PowerShell 5.1 or later).

.DESCRIPTION
    Declare an incident, push IOCs and host details, comment and list tasks from the machine you're
    working on, with an ost_ API token. The same commands as clients/ost on macOS and Linux.

      ost.ps1 login [URL]
      ost.ps1 logout
      ost.ps1 whoami [--json]
      ost.ps1 incidents [--status open|closed] [--hunts] [--json]
      ost.ps1 declare TITLE --severity S [--tlp T] [--summary TEXT] [--tag T]...
      ost.ps1 ioc add REF [VALUE...] [--verdict V] [--note TEXT] [--tag T]...
      ost.ps1 host add REF [--hostname H] [--ip IP] [--os OS] [--user U] [--criticality S]
                           [--patient-zero] [--note TEXT]
      ost.ps1 comment REF [TEXT...]
      ost.ps1 tasks [REF] [--mine] [--json]

    With no VALUE, `ioc add` reads indicators one per line from the pipeline or redirected input
    (Get-Content iocs.txt | .\ost.ps1 ioc add OST-2026-001), or asks for them, ending at a blank line.

    Settings: OSTRAKA_URL and OSTRAKA_TOKEN override %APPDATA%\Ostraka\client, which `login` writes
    readable by you alone. Plain http:// is refused for anything but this computer unless
    OSTRAKA_ALLOW_HTTP=1.

    Exit status: 0 done, 1 failed, 2 usage, 3 the token wasn't accepted (401), 4 not allowed (403),
    5 conflict (409).
#>

# No param() block: arguments arrive in $args exactly as typed, so `--severity` works as it does
# with the Bash client instead of being taken for a PowerShell parameter.
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$OstVersion = '0.1.0'
$RefPattern = '^(OST|HNT)-[0-9]{4}-[0-9]{3,}$'
$TokenPattern = '^ost_[A-Za-z0-9_-]+$'
$Severities = @('critical', 'high', 'medium', 'low')
$Tlps = @('red', 'amber+strict', 'amber', 'green', 'clear')
$Verdicts = @('unknown', 'suspicious', 'malicious', 'benign')

if ($env:APPDATA) {
    $ConfigDir = Join-Path $env:APPDATA 'Ostraka'
} else {
    $ConfigDir = Join-Path $HOME '.config/ostraka'  # pwsh on macOS or Linux
}
$ConfigFile = Join-Path $ConfigDir 'client'

# Windows PowerShell 5.1 still offers TLS 1.0 first on older .NET; servers behind Caddy or nginx won't.
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor
        [Net.SecurityProtocolType]::Tls12
} catch { }

class OstExit : System.Exception {
    [int] $Code
    OstExit([string] $message, [int] $code) : base($message) { $this.Code = $code }
}

function Fail([string] $Message, [int] $Code = 1) { throw [OstExit]::new($Message, $Code) }
function UsageFail([string] $Message) { Fail "$Message`nRun 'ost.ps1 help' for usage." 2 }

function Show-Usage {
    @'
ost: the Ostraka responder client

  ost.ps1 login [URL]                    save the server and an API token (asked for, never shown)
  ost.ps1 logout                         forget them
  ost.ps1 whoami [--json]
  ost.ps1 incidents [--status open|closed] [--hunts] [--json]
  ost.ps1 declare TITLE --severity S [--tlp T] [--summary TEXT] [--tag T]...
  ost.ps1 ioc add REF [VALUE...] [--verdict V] [--note TEXT] [--tag T]...
                                         no VALUE: one per line from the pipeline or input
  ost.ps1 host add REF [--hostname H] [--ip IP] [--os OS] [--user U] [--criticality S]
                       [--patient-zero] [--note TEXT]
                                         anything not given is read from this machine
  ost.ps1 comment REF [TEXT...]          no TEXT: read from the pipeline or input
  ost.ps1 tasks [REF] [--mine] [--json]  no REF: your tasks and your team's queue
  ost.ps1 version

REF is an incident or hunt reference such as OST-2026-001.
Settings: OSTRAKA_URL and OSTRAKA_TOKEN, or %APPDATA%\Ostraka\client from `login`.
'@
}

# ---- settings -------------------------------------------------------------------------------------

function Read-Config {
    $conf = @{ url = ''; token = '' }
    if (Test-Path -LiteralPath $ConfigFile) {
        # key=value lines, read as data rather than dot-sourced as a script.
        foreach ($line in Get-Content -LiteralPath $ConfigFile) {
            $i = $line.IndexOf('=')
            if ($i -gt 0) { $conf[$line.Substring(0, $i)] = $line.Substring($i + 1) }
        }
    }
    if ($env:OSTRAKA_URL) { $conf.url = $env:OSTRAKA_URL }
    if ($env:OSTRAKA_TOKEN) { $conf.token = $env:OSTRAKA_TOKEN }
    $conf.url = ([string]$conf.url).TrimEnd('/')
    return $conf
}

function Test-ServerUrl([string] $Url) {
    if ($Url -match '^https://') { return }
    # http://127.0.0.1:1@evil.example/ is evil.example with a user name, and the token would go there in clear.
    if ($Url -match '^http://[^/?#]*@') {
        Fail "$Url has user info (the part before @) in it: the request would go to what follows the @ with the token unencrypted. Give the server's address alone."
    }
    if ($Url -match '^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)') { return }
    if ($Url -match '^http://') {
        if ($env:OSTRAKA_ALLOW_HTTP -eq '1') { return }
        Fail "$Url is plain HTTP: the token would cross the network unencrypted. Use the server's https:// address (or set OSTRAKA_ALLOW_HTTP=1 if you really mean it)."
    }
    Fail "The server address must start with https:// (got '$Url')." 2
}

$Script:Server = ''
$Script:Token = ''

function Use-Config {
    $conf = Read-Config
    if (-not $conf.url) { Fail "No server set. Run 'ost.ps1 login', or set OSTRAKA_URL and OSTRAKA_TOKEN." 2 }
    if (-not $conf.token) { Fail "No API token set. Run 'ost.ps1 login', or set OSTRAKA_TOKEN." 2 }
    Test-ServerUrl $conf.url
    if ($conf.token -notmatch $TokenPattern) {
        Fail "That isn't an Ostraka API token (they start ost_). Make one under Profile -> API tokens." 2
    }
    $Script:Server = $conf.url
    $Script:Token = $conf.token
}

# ---- HTTP -----------------------------------------------------------------------------------------

# Returns @{ Code; Body } and never throws for an HTTP error, so callers decide what a 404 means.
function Invoke-Ost([string] $Method, [string] $Path, $Body = $null) {
    $headers = @{ Accept = 'application/json' }
    if ($Script:Token) { $headers.Authorization = "Bearer $($Script:Token)" }
    $params = @{
        Uri             = "$($Script:Server)$Path"
        Method          = $Method
        Headers         = $headers
        UserAgent       = "ost/$OstVersion"
        UseBasicParsing = $true
        TimeoutSec      = 120
    }
    if ($null -ne $Body) {
        # Bytes, not a string: 5.1 would otherwise send the body as ISO-8859-1.
        $json = ConvertTo-Json -InputObject $Body -Depth 6 -Compress
        $params.Body = [Text.Encoding]::UTF8.GetBytes($json)
        $params.ContentType = 'application/json; charset=utf-8'
    }
    try {
        $resp = Invoke-WebRequest @params
        $text = $resp.Content
        if ($text -is [byte[]]) { $text = [Text.Encoding]::UTF8.GetString($text) }
        return @{ Code = [int]$resp.StatusCode; Body = $text }
    } catch {
        $response = $null
        if ($_.Exception.PSObject.Properties['Response']) { $response = $_.Exception.Response }
        if ($null -eq $response) { return @{ Code = 0; Body = '' } }
        $text = ''
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $text = $_.ErrorDetails.Message
        } elseif ($response -is [System.Net.HttpWebResponse]) {
            try {
                $reader = New-Object IO.StreamReader($response.GetResponseStream())
                $text = $reader.ReadToEnd()
            } catch { }
        }
        return @{ Code = [int]$response.StatusCode; Body = $text }
    }
}

function ConvertFrom-OstJson([string] $Text) {
    if (-not $Text) { return $null }
    try { return ConvertFrom-Json -InputObject $Text } catch { return $null }
}

function Get-Detail([string] $Text) {
    $obj = ConvertFrom-OstJson $Text
    if ($null -eq $obj -or -not $obj.PSObject.Properties['detail']) { return '' }
    $d = $obj.detail
    if ($d -is [string]) { return $d }
    $parts = foreach ($e in @($d)) {
        $loc = @()
        if ($e.PSObject.Properties['loc']) { $loc = @($e.loc | Select-Object -Skip 1) }
        $field = ($loc -join '.')
        if ($field) { "${field}: $($e.msg)" } else { "$($e.msg)" }
    }
    return ($parts -join '; ')
}

# The sentence and exit status for a failed request.
function Get-Problem($Resp) {
    $detail = Get-Detail $Resp.Body
    switch -Regex ([string]$Resp.Code) {
        '^0$' { return @("Couldn't reach $($Script:Server). Check the address and that the server is running.", 1) }
        '^401$' {
            $why = if ($detail) { " ($detail)" } else { '' }
            return @("The server didn't accept your token$why. It may have been revoked or have expired: make a new one under Profile -> API tokens, then run 'ost.ps1 login'.", 3)
        }
        '^403$' {
            if ($detail -match 'read-only') {
                return @('This token is read-only (an MCP token). Make a full one under Profile -> API tokens.', 4)
            }
            if (-not $detail) { $detail = 'you do not have permission to do that.' }
            return @("Not allowed: $detail", 4)
        }
        '^404$' { if ($detail) { return @($detail, 1) } else { return @('Not found.', 1) } }
        '^409$' {
            if (-not $detail) { $detail = 'someone changed it first. Fetch it again and retry.' }
            return @("Conflict: $detail", 5)
        }
        '^422$' { return @("The server refused that: $detail", 1) }
        '^429$' { if ($detail) { return @($detail, 1) } else { return @('Too many requests; try again shortly.', 1) } }
        '^5' { return @("The server had a problem (HTTP $($Resp.Code)): $detail", 1) }
        default { return @("Unexpected answer (HTTP $($Resp.Code)): $detail", 1) }
    }
}

function Assert-Ok($Resp) {
    if ($Resp.Code -ge 200 -and $Resp.Code -lt 300) { return (ConvertFrom-OstJson $Resp.Body) }
    $p = Get-Problem $Resp
    Fail $p[0] $p[1]
}

function Resolve-Ref([string] $Ref) {
    $r = $Ref.TrimStart('#').ToUpperInvariant()
    if ($r -notmatch $RefPattern) { UsageFail "'$Ref' isn't an incident or hunt reference (like OST-2026-001)." }
    $resp = Invoke-Ost GET "/api/incidents/by-ref/$r"
    if ($resp.Code -eq 404) { Fail "No incident $r, or not one you can see." }
    $incident = Assert-Ok $resp
    return @{ Id = $incident.id; Ref = $r }
}

# ---- arguments ------------------------------------------------------------------------------------

# Splits `--name value`, `--name=value` and bare words. $Flags take no value.
function Split-Args([object[]] $Argv, [string[]] $WithValue, [string[]] $Flags) {
    $opts = @{}
    $words = New-Object System.Collections.Generic.List[string]
    $multi = @('--tag')
    for ($i = 0; $i -lt $Argv.Count; $i++) {
        $a = [string]$Argv[$i]
        $name = $a; $value = $null
        if ($a -match '^(--[a-z-]+)=(.*)$') { $name = $Matches[1]; $value = $Matches[2] }
        if ($WithValue -contains $name) {
            if ($null -eq $value) {
                if ($i + 1 -ge $Argv.Count) { UsageFail "$name needs a value." }
                $i++; $value = [string]$Argv[$i]
            }
            if ($multi -contains $name) {
                if (-not $opts.ContainsKey($name)) { $opts[$name] = @() }
                $opts[$name] += $value
            } else { $opts[$name] = $value }
        } elseif ($Flags -contains $name) {
            $opts[$name] = $true
        } elseif ($a -eq '-' -or $a -notmatch '^-') {
            $words.Add($a)
        } else {
            UsageFail "Unknown option '$a'."
        }
    }
    return @{ Opts = $opts; Words = $words.ToArray() }
}

function Get-Opt($Parsed, [string] $Name, $Default = '') {
    if ($Parsed.Opts.ContainsKey($Name)) { return $Parsed.Opts[$Name] } else { return $Default }
}

# Lines from the pipeline, redirected input, or typed in, ending at a blank line.
function Read-InputLines([object[]] $Piped, [string] $Prompt) {
    if ($Piped.Count -gt 0) { return @($Piped | ForEach-Object { [string]$_ }) }
    if ([Console]::IsInputRedirected) { return @([Console]::In.ReadToEnd() -split "`r?`n") }
    Write-Host $Prompt
    $lines = @()
    while ($true) {
        $l = Read-Host
        if (-not $l) { break }
        $lines += $l
    }
    return $lines
}

function Format-Table2([string[]] $Header, [object[]] $Rows) {
    # Our own padding rather than Format-Table, whose truncation hides the end of titles.
    $all = @(, $Header) + $Rows
    $widths = @()
    for ($c = 0; $c -lt $Header.Count; $c++) {
        $widths += ($all | ForEach-Object { ([string]$_[$c]).Length } | Measure-Object -Maximum).Maximum
    }
    foreach ($row in $all) {
        $cells = for ($c = 0; $c -lt $Header.Count; $c++) {
            if ($c -lt $Header.Count - 1) { ([string]$row[$c]).PadRight($widths[$c]) } else { [string]$row[$c] }
        }
        Write-Output ($cells -join '  ')
    }
}

function Get-Path($Obj, [string] $Path) {
    $v = $Obj
    foreach ($k in $Path.Split('.')) {
        if ($null -eq $v -or -not $v.PSObject.Properties[$k]) { return '' }
        $v = $v.$k
    }
    if ($null -eq $v) { return '' }
    # PowerShell 7's ConvertFrom-Json turns ISO timestamps into DateTime; put them back as ISO, in UTC.
    if ($v -is [datetime]) { return $v.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') }
    return [string]$v
}

# ---- commands -------------------------------------------------------------------------------------

function Invoke-Login([object[]] $Argv, [object[]] $Piped) {
    if ($Argv.Count -gt 1) { UsageFail 'login takes at most a server address.' }
    $url = if ($Argv.Count -eq 1) { [string]$Argv[0] } else { '' }
    if (-not $url) {
        $current = (Read-Config).url
        $answer = Read-Host "Server address$(if ($current) { " [$current]" })"
        $url = if ($answer) { $answer } else { $current }
    }
    if (-not $url) { UsageFail 'Give the server address, e.g. ost.ps1 login https://ostraka.example.org' }
    $url = $url.TrimEnd('/')
    Test-ServerUrl $url
    $Script:Server = $url
    $Script:Token = ''

    # Ask the server what it is before handing it a token.
    $meta = Invoke-Ost GET '/api/meta'
    if ($meta.Code -eq 0) { Assert-Ok $meta | Out-Null }
    $info = ConvertFrom-OstJson $meta.Body
    if ($meta.Code -ne 200 -or $null -eq $info -or (Get-Path $info 'name') -ne 'Ostraka') {
        Fail "$url doesn't answer as an Ostraka server."
    }

    if ($Piped.Count -gt 0) {
        $token = [string]$Piped[0]
    } elseif ([Console]::IsInputRedirected) {
        $token = [Console]::In.ReadLine()
    } else {
        $secure = Read-Host 'API token (Profile -> API tokens; not shown)' -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    }
    $token = ([string]$token).Trim()
    if ($token -notmatch $TokenPattern) { Fail "That isn't an Ostraka API token (they start ost_)." 2 }
    $Script:Token = $token
    $me = Assert-Ok (Invoke-Ost GET '/api/users/me')

    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    Set-Content -LiteralPath $ConfigFile -Value @("url=$url", "token=$token") -Encoding ASCII
    Protect-ConfigFile
    Write-Output "Signed in to $url as $($me.name) <$($me.email)>. Saved to $ConfigFile."
}

# Readable by you alone: inheritance off, one rule for the current user.
function Protect-ConfigFile {
    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        & chmod 600 $ConfigFile
        return
    }
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($me, 'FullControl', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $ConfigFile -AclObject $acl
}

function Invoke-Logout {
    if (Test-Path -LiteralPath $ConfigFile) {
        Remove-Item -LiteralPath $ConfigFile -Force
        Write-Output "Removed $ConfigFile. The token still works until you revoke it under Profile -> API tokens."
    } else { Write-Output 'Nothing saved.' }
}

function Invoke-Whoami([object[]] $Argv) {
    $p = Split-Args $Argv @() @('--json')
    Use-Config
    $resp = Invoke-Ost GET '/api/users/me'
    $me = Assert-Ok $resp
    if (Get-Opt $p '--json' $false) { Write-Output $resp.Body; return }
    $team = Get-Path $me 'team.name'
    Write-Output "$($me.name) <$($me.email)>"
    Write-Output "$($me.role)$(if ($team) { ", $team" }) on $($Script:Server)"
}

function Invoke-Incidents([object[]] $Argv) {
    $p = Split-Args $Argv @('--status') @('--json', '--hunts')
    $status = ([string](Get-Opt $p '--status' 'open')).ToLowerInvariant()
    if (@('open', 'closed') -notcontains $status) { UsageFail '--status is open or closed.' }
    $kind = if (Get-Opt $p '--hunts' $false) { 'hunt' } else { 'incident' }
    Use-Config
    $resp = Invoke-Ost GET "/api/incidents?status=$status&kind=$kind"
    $list = @(Assert-Ok $resp)
    if (Get-Opt $p '--json' $false) { Write-Output $resp.Body; return }
    if ($list.Count -eq 0) { Write-Output "No $status ${kind}s you can see."; return }
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($i in $list) {
        $rows.Add(@($i.ref, $i.severity, $i.tlp, $i.phase, (Get-Path $i 'commander.name'), $i.title))
    }
    Format-Table2 @('REF', 'SEVERITY', 'TLP', 'PHASE', 'COMMANDER', 'TITLE') $rows.ToArray()
}

function ConvertTo-Tlp([string] $Value) { return ($Value.ToLowerInvariant() -replace '^tlp:', '') }

function Invoke-Declare([object[]] $Argv) {
    $p = Split-Args $Argv @('--severity', '-s', '--tlp', '-t', '--summary', '--tag') @()
    $title = ($p.Words -join ' ').Trim()
    $severity = ([string](Get-Opt $p '--severity' (Get-Opt $p '-s' ''))).ToLowerInvariant()
    $tlp = ConvertTo-Tlp ([string](Get-Opt $p '--tlp' (Get-Opt $p '-t' 'amber')))
    if (-not $title) { UsageFail 'declare needs a title: ost.ps1 declare "Ransom note on FS01" --severity high' }
    if (-not $severity) { UsageFail "declare needs --severity: one of $($Severities -join ' ')." }
    if ($Severities -notcontains $severity) { UsageFail "Severity is one of: $($Severities -join ' ')." }
    if ($Tlps -notcontains $tlp) { UsageFail "TLP is one of: $($Tlps -join ' ')." }
    Use-Config
    $body = [ordered]@{
        kind = 'incident'; title = $title; severity = $severity; tlp = $tlp
        summary = [string](Get-Opt $p '--summary' ''); tags = @(Get-Opt $p '--tag' @())
    }
    $incident = Assert-Ok (Invoke-Ost POST '/api/incidents' $body)
    Write-Output "Declared $($incident.ref): $title ($severity, TLP:$($tlp.ToUpperInvariant()))"
    Write-Output "$($Script:Server)/incidents/$($incident.ref)"
}

function Invoke-Ioc([object[]] $Argv, [object[]] $Piped) {
    if ($Argv.Count -lt 2 -or $Argv[0] -ne 'add') { UsageFail 'Usage: ost.ps1 ioc add REF [VALUE...]' }
    $ref = [string]$Argv[1]
    $p = Split-Args @($Argv | Select-Object -Skip 2) @('--verdict', '--note', '--tag') @()
    $verdict = ([string](Get-Opt $p '--verdict' 'unknown')).ToLowerInvariant()
    if ($Verdicts -notcontains $verdict) { UsageFail "Verdict is one of: $($Verdicts -join ' ')." }
    $values = @($p.Words | Where-Object { $_ -ne '-' })
    if ($values.Count -eq 0) {
        # Blank lines and # comments let a pasted list keep its headings.
        $values = @(Read-InputLines $Piped 'Paste indicators, one per line, then a blank line:' |
            ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
    }
    if ($values.Count -eq 0) { UsageFail 'No indicators given.' }
    Use-Config
    $incident = Resolve-Ref $ref
    $added = 0; $failed = 0; $lastExit = 0
    foreach ($value in $values) {
        # The server detects the type and refangs (hxxp, [.]), so values go as written.
        $body = [ordered]@{
            value = $value; verdict = $verdict; note = [string](Get-Opt $p '--note' ''); tags = @(Get-Opt $p '--tag' @())
        }
        $resp = Invoke-Ost POST "/api/incidents/$($incident.Id)/observables" $body
        if ($resp.Code -ge 200 -and $resp.Code -lt 300) {
            $added++
            $o = ConvertFrom-OstJson $resp.Body
            $shown = Get-Path $o 'value'
            if (-not $shown) { $shown = $value }
            Write-Output "$($o.ref)`t$($o.kind)`t$shown"
        } else {
            $failed++
            $problem = Get-Problem $resp
            $lastExit = $problem[1]
            [Console]::Error.WriteLine("ost: ${value}: $($problem[0])")
            if (@(0, 401, 403) -contains $resp.Code) { break }  # it won't fix itself on the next line
        }
    }
    $tail = if ($failed) { ", $failed failed" } else { '' }
    [Console]::Error.WriteLine("Added $added to $($incident.Ref)$tail.")
    if ($failed) { throw [OstExit]::new('', $lastExit) }
}

function Get-ThisMachine {
    $os = ''
    try {
        $info = Get-CimInstance Win32_OperatingSystem
        $os = "$($info.Caption) $($info.Version)".Trim()
    } catch {
        $os = [Environment]::OSVersion.VersionString
    }
    $ips = @()
    try {
        foreach ($nic in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
            if ($nic.OperationalStatus -ne 'Up') { continue }
            foreach ($a in $nic.GetIPProperties().UnicastAddresses) {
                $ip = $a.Address
                if ([Net.IPAddress]::IsLoopback($ip) -or $ip.IsIPv6LinkLocal) { continue }
                $ips += ($ip.ToString() -replace '%.*$', '')
            }
        }
    } catch { }
    $user = if ($env:USERDOMAIN) { "$($env:USERDOMAIN)\$($env:USERNAME)" } else { [Environment]::UserName }
    return @{ Hostname = [Environment]::MachineName; Os = $os; Ips = @($ips | Select-Object -Unique); User = $user }
}

function Invoke-Host([object[]] $Argv) {
    if ($Argv.Count -lt 2 -or $Argv[0] -ne 'add') { UsageFail 'Usage: ost.ps1 host add REF [--hostname H] ...' }
    $ref = [string]$Argv[1]
    $p = Split-Args @($Argv | Select-Object -Skip 2) @('--hostname', '--ip', '--os', '--user', '--criticality', '--note') @('--patient-zero')
    $criticality = ([string](Get-Opt $p '--criticality' 'medium')).ToLowerInvariant()
    if ($Severities -notcontains $criticality) { UsageFail "Criticality is one of: $($Severities -join ' ')." }
    $hostname = [string](Get-Opt $p '--hostname' '')
    $ip = [string](Get-Opt $p '--ip' ''); $os = [string](Get-Opt $p '--os' ''); $user = [string](Get-Opt $p '--user' '')
    $note = [string](Get-Opt $p '--note' '')
    # A host named on the command line is some other machine: don't fill it in with this one's details.
    if (-not $hostname) {
        $m = Get-ThisMachine
        $hostname = $m.Hostname
        if (-not $os) { $os = $m.Os }
        if (-not $user) { $user = $m.User }
        if (-not $ip -and $m.Ips.Count) { $ip = $m.Ips[0] }
        $extra = "Added with ost from the host itself at $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))."
        if ($m.Ips.Count -gt 1) { $extra += " Addresses: $($m.Ips -join ', ')." }
        $note = if ($note) { "$note`n`n$extra" } else { $extra }
    }
    Use-Config
    $incident = Resolve-Ref $ref
    $body = [ordered]@{
        hostname = $hostname; ip = $ip; os = $os; primary_user = $user; criticality = $criticality
        is_patient_zero = [bool](Get-Opt $p '--patient-zero' $false); notes = $note
    }
    $asset = Assert-Ok (Invoke-Ost POST "/api/incidents/$($incident.Id)/assets" $body)
    $bits = @($hostname, $ip, $os, $user) | Where-Object { $_ }
    Write-Output "Added $($asset.ref) to $($incident.Ref): $($bits -join ', ')"
}

function Invoke-Comment([object[]] $Argv, [object[]] $Piped) {
    if ($Argv.Count -lt 1) { UsageFail 'Usage: ost.ps1 comment REF TEXT' }
    $ref = [string]$Argv[0]
    $text = (@($Argv | Select-Object -Skip 1) -join ' ')
    if (-not $text) { $text = (Read-InputLines $Piped 'Write the comment, then a blank line:') -join "`n" }
    if (-not $text.Trim()) { UsageFail 'The comment is empty.' }
    Use-Config
    $incident = Resolve-Ref $ref
    Assert-Ok (Invoke-Ost POST "/api/incidents/$($incident.Id)/comments" @{ body = $text.TrimEnd() }) | Out-Null
    Write-Output "Commented on $($incident.Ref)."
}

function Format-Due([string] $Value) {
    if ($Value -match '^(\d{4}-\d\d-\d\d)T(\d\d:\d\d)') { return "$($Matches[1]) $($Matches[2])" }
    return $Value
}

function Invoke-Tasks([object[]] $Argv) {
    $p = Split-Args $Argv @() @('--mine', '--json')
    if ($p.Words.Count -gt 1) { UsageFail 'tasks takes one reference.' }
    Use-Config
    $mine = [bool](Get-Opt $p '--mine' $false)
    $meId = ''
    if ($mine) { $meId = (Assert-Ok (Invoke-Ost GET '/api/users/me')).id }
    $incident = $null
    if ($p.Words.Count -eq 1) {
        $incident = Resolve-Ref $p.Words[0]
        $resp = Invoke-Ost GET "/api/incidents/$($incident.Id)/tasks"
    } else {
        $resp = Invoke-Ost GET '/api/tasks/mine'
    }
    $tasks = @(Assert-Ok $resp)
    if (Get-Opt $p '--json' $false) { Write-Output $resp.Body; return }
    # --mine keeps what's assigned to you, dropping the team queue.
    if ($mine) { $tasks = @($tasks | Where-Object { (Get-Path $_ 'assignee.id') -eq $meId }) }
    if ($tasks.Count -eq 0) { Write-Output "No tasks$(if ($mine) { ' assigned to you' })."; return }
    # Rows go in a list: a lone row collected from a pipeline would unroll into its cells.
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($t in $tasks) {
        $due = Format-Due (Get-Path $t 'due_at')
        $who = Get-Path $t 'assignee.name'; $team = Get-Path $t 'team.name'
        if ($incident) { $rows.Add(@($t.ref, $t.status, $who, $team, $due, $t.title)) }
        else { $rows.Add(@($t.ref, (Get-Path $t 'incident.ref'), $t.status, $who, $team, $due, $t.title)) }
    }
    if ($incident) {
        Format-Table2 @('TASK', 'STATUS', 'ASSIGNEE', 'TEAM', 'DUE', 'TITLE') $rows.ToArray()
    } else {
        Format-Table2 @('TASK', 'INCIDENT', 'STATUS', 'ASSIGNEE', 'TEAM', 'DUE', 'TITLE') $rows.ToArray()
    }
}

# ---- main -----------------------------------------------------------------------------------------

$piped = @($input)
$command = if ($args.Count -gt 0) { [string]$args[0] } else { 'help' }
$rest = @($args | Select-Object -Skip 1)
try {
    switch ($command) {
        'login' { Invoke-Login $rest $piped }
        'logout' { Invoke-Logout }
        'whoami' { Invoke-Whoami $rest }
        'incidents' { Invoke-Incidents $rest }
        'declare' { Invoke-Declare $rest }
        'ioc' { Invoke-Ioc $rest $piped }
        'host' { Invoke-Host $rest }
        'comment' { Invoke-Comment $rest $piped }
        'tasks' { Invoke-Tasks $rest }
        { $_ -in 'version', '--version' } { Write-Output "ost $OstVersion" }
        { $_ -in 'help', '-h', '--help' } { Show-Usage }
        default { UsageFail "Unknown command '$command'." }
    }
} catch [OstExit] {
    if ($_.Exception.Message) { [Console]::Error.WriteLine("ost: $($_.Exception.Message)") }
    exit $_.Exception.Code
}
exit 0
