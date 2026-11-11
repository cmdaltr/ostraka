# Ostraka.ps1's logic that needs no Windows: addresses, has_admin, the first admin and update flags,
# with the CLI stubbed. Run in PowerShell on Linux with the repo at /src (tests/install/test_windows_launcher.py).
$ErrorActionPreference = 'Stop'
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile('/src/desktop/windows/Ostraka.ps1', [ref]$null, [ref]$errors) | Out-Null
if ($errors) { $errors | ForEach-Object { Write-Host "PARSE $_" }; exit 1 }
Write-Host 'ok   parses'

$work = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid())
New-Item -ItemType Directory $work | Out-Null
$env:APPDATA = "$work/appdata"; $env:LOCALAPPDATA = "$work/local"; $env:USERPROFILE = "$work/home"
. /src/desktop/windows/Ostraka.ps1
$failures = 0
function Check($what, $want, $got) {
    if ("$want" -ceq "$got") { [Console]::WriteLine("ok   $what") } else { [Console]::WriteLine("FAIL $what`: expected '$want', got '$got'"); $script:failures++ }
}

# Addresses: the path, query and fragment go, as on Linux and macOS
Check 'path dropped' 'https://ir.example.org:8443' (ConvertTo-ServerAddress ' https://ir.example.org:8443/incidents/OST-1 ')
Check 'trailing slash' 'https://ir.example.org' (ConvertTo-ServerAddress 'https://ir.example.org/')
Check 'query and fragment dropped' 'https://ir.example.org' (ConvertTo-ServerAddress 'ir.example.org/x?tab=1#a')
Check 'bare host' 'https://ostraka.example.org' (ConvertTo-ServerAddress 'ostraka.example.org')
Check 'http refused' '' (ConvertTo-ServerAddress 'http://ir.example.org')
Check 'http loopback' 'http://127.0.0.1:24747' (ConvertTo-ServerAddress 'http://127.0.0.1:24747/')
Check 'http when IT sets it' 'http://lab.internal' (ConvertTo-ServerAddress 'http://lab.internal/ostraka' -AllowHttp)
Check 'user info refused' '' (ConvertTo-ServerAddress 'https://user:pw@ir.example.org')
Check 'local' 'local' (ConvertTo-ServerAddress 'local')
Check 'CLI path in the new layout' 'desktop\windows\ostraka-cli.ps1' $CliPath

# has_admin
$paths = Get-AppPaths
New-Item -ItemType Directory -Force $paths.State | Out-Null
$script:statusJson = ''
function Invoke-OstrakaCli { param([string[]]$Arguments, [switch]$Progress)
    if ($Arguments[0] -eq 'status') { return @{ Code = 0; Output = @($script:statusJson) } }
    $script:faCalls++
    $code = $script:faCodes[[math]::Min($script:faCalls - 1, $script:faCodes.Count - 1)]
    $out = if ($code -eq 0) { @('http://127.0.0.1:24747/invite/tok') } else { @('error: database not ready') }
    return @{ Code = $code; Output = $out }
}
foreach ($case in @(
    @{ Json = '{"running": false, "has_admin": null}'; Pending = $false; Want = $false; Name = 'null, no marker: not asked' },
    @{ Json = '{"running": false, "has_admin": null}'; Pending = $true; Want = $true; Name = 'null, marker: asked' },
    @{ Json = '{"running": true}'; Pending = $true; Want = $true; Name = 'missing, marker: asked' },
    @{ Json = '{"running": true, "has_admin": false}'; Pending = $false; Want = $true; Name = 'false: asked' },
    @{ Json = '{"running": true, "has_admin": true}'; Pending = $true; Want = $false; Name = 'true: not asked' })) {
    Remove-Item -Force $paths.AdminPending -ErrorAction SilentlyContinue
    if ($case.Pending) { Set-Content $paths.AdminPending 'x' }
    $script:statusJson = $case.Json
    Check "has_admin $($case.Name)" $case.Want (Test-NeedsFirstAdmin)
}
Check 'has_admin true clears the marker' $false (Test-Path $paths.AdminPending)

# The first admin: 0 and 5 settle it, anything else keeps the marker
function Write-Host { }
foreach ($case in @(
    @{ Codes = @(0); Link = 'http://127.0.0.1:24747/invite/tok'; Pending = $false; Name = '0 opens the link' },
    @{ Codes = @(5); Link = ''; Pending = $false; Name = '5 is an admin already' },
    @{ Codes = @(1); Link = ''; Pending = $true; Name = '1 keeps asking' },
    @{ Codes = @(3); Link = ''; Pending = $true; Name = '3 keeps asking' },
    @{ Codes = @(1, 0); Link = 'http://127.0.0.1:24747/invite/tok'; Pending = $false; Name = '1 then a retry that works' })) {
    Set-Content $paths.AdminPending 'x'
    $script:faCodes = $case.Codes; $script:faCalls = 0
    $script:answers = [System.Collections.Generic.Queue[string]]::new()
    foreach ($a in @('admin@example.org', 'admin@example.org', '')) { $script:answers.Enqueue($a) }
    function Read-Host { param($Prompt) $script:answers.Dequeue() }
    $got = Read-FirstAdmin
    Check "first-admin $($case.Name)" $case.Link "$got"
    Check "first-admin $($case.Name): marker" $case.Pending (Test-Path $paths.AdminPending)
}
Remove-Item Function:\Write-Host

# The update mirrors only a copy this app made (robocopy isn't on Linux: its arguments are checked)
$script:robo = @()
function robocopy { $script:robo = $args; $global:LASTEXITCODE = 1 }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$src = "$work/src"; New-Item -ItemType Directory -Force "$src/ostraka" | Out-Null
Set-Content "$src/ostraka/docker-compose.yml" 'services: {}'
$AppDir = "$work/app"; New-Item -ItemType Directory -Force $AppDir | Out-Null
[System.IO.Compression.ZipFile]::CreateFromDirectory($src, "$AppDir/source.zip")
$dest = "$work/Ostraka"
Expand-Source $dest '0.1.0'
Check 'first unpack merges' '/E' $script:robo[2]
Expand-Source $dest '0.2.0'
Check 'an update mirrors' '/MIR' $script:robo[2]
$rest = $script:robo -join ' '
Check 'mirror skips junctions' $true ($rest -match ' /XJ ')
Check 'mirror keeps .env and the marker' $true ($rest -match '/XF \.env \.ostraka-version /XD \.git')
Check 'marker rewritten' '0.2.0' (Get-Content "$dest/.ostraka-version" -Raw).Trim()
New-Item -ItemType Directory -Force "$dest/.git" | Out-Null
Expand-Source $dest '0.3.0'
Check 'a checkout is never mirrored' '/E' $script:robo[2]

Remove-Item -Recurse -Force $work
if ($failures) { Write-Host "$failures failed."; exit 1 }
Write-Host 'All passed.'
