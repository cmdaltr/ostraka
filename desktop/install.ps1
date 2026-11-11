<#
Install or upgrade Ostraka on Windows 10 or 11 (x64 or ARM64) from a GitHub release (desktop/README.md).

    powershell -ExecutionPolicy Bypass -File install.ps1                          the latest release
    powershell -ExecutionPolicy Bypass -File install.ps1 -Version v0.2.0          a given release
    powershell -ExecutionPolicy Bypass -File install.ps1 -File Ostraka-0.2.0-windows-universal.msi
                                                                                  a package already downloaded,
                                                                                  installed as it is (no checksum)
    powershell -ExecutionPolicy Bypass -File install.ps1 -FromDir dist\0.2.0      a release in a folder, checked
                                                                                  against its SHA256SUMS
    powershell -ExecutionPolicy Bypass -File install.ps1 -ServerUrl https://ostraka.example.org
                                                                                  connect to your team's server
                                                                                  (the MSI's SERVERURL)
    powershell -ExecutionPolicy Bypass -File install.ps1 -DownloadOnly DIR        download and check, install nothing
    powershell -ExecutionPolicy Bypass -File install.ps1 -Launch                  open Ostraka when it's installed
    powershell -ExecutionPolicy Bypass -File install.ps1 -Clean                   once the new package passes its
                                                                                  checksum, run uninstall.ps1 -Data
                                                                                  first (it asks; -Yes doesn't)

The repository may be private: set GH_TOKEN (or GITHUB_TOKEN) to a token that can read it, or be
logged in to the gh CLI. It installs for the current user, so it needs no administrator rights. Run it
as a file, never piped into PowerShell. Works in Windows PowerShell 5.1 and pwsh.
#>
param(
    [string]$Version = "latest",
    [string]$File,
    [string]$FromDir,
    [string]$ServerUrl,
    [string]$DownloadOnly,
    [switch]$Launch,
    [switch]$Clean,
    [switch]$Yes
)
$ErrorActionPreference = "Stop"
# Windows PowerShell's progress bar slows downloads by an order of magnitude
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$repo = if ($env:OSTRAKA_REPO) { $env:OSTRAKA_REPO } else { "cmdaltr/ostraka" }
# One MSI for x64 and ARM64, named as Rowanholt's (desktop/README.md): the launcher is PowerShell and
# the stack runs in Docker. 32-bit Windows has no Docker Desktop, so it's refused. The processor, not
# this process: a 32-bit PowerShell on 64-bit Windows says x86 in PROCESSOR_ARCHITECTURE
$pattern = "Ostraka-*-windows-universal.msi"
$cpu = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
if ($env:OSTRAKA_CPU) { $cpu = $env:OSTRAKA_CPU }  # only the tests set it
if ($cpu -notin "AMD64", "ARM64") { throw "No Ostraka for $cpu Windows: 64-bit Windows (x64 or ARM64) is covered" }

if ($File -and $FromDir) { throw "-File and -FromDir are two different sources: give one" }
if ($File -and $DownloadOnly) { throw "-DownloadOnly has nothing to download with -File" }
if ($Version -ne "latest" -and -not $Version.StartsWith("v")) { $Version = "v$Version" }
if ($ServerUrl -and $ServerUrl -notmatch '^https://' -and $ServerUrl -notmatch '^http://(127\.0\.0\.1|localhost)(:[0-9]+)?(/|$)') {
    throw "-ServerUrl must be an https:// address (plain http only for this computer)"
}

$script:step = 0
$script:steps = 0
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

# Invoke-WebRequest's own progress bar is what makes it slow in Windows PowerShell: read the stream
# here instead, and print the percentage on one line
function Get-Download($url, $headers, $out) {
    $req = [Net.HttpWebRequest]::Create($url)
    foreach ($k in $headers.Keys) {
        if ($k -eq "Accept") { $req.Accept = $headers[$k] } else { $req.Headers[$k] = $headers[$k] }
    }
    $req.UserAgent = "ostraka-install"
    $resp = $req.GetResponse()
    $total = $resp.ContentLength
    $in = $resp.GetResponseStream()
    $fs = [IO.File]::Create($out)
    try {
        $buf = New-Object byte[] (1MB)
        $got = 0
        $shown = -1
        while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
            $fs.Write($buf, 0, $n)
            $got += $n
            if ($total -gt 0) {
                $pct = [int](100 * $got / $total)
                if ($pct -ne $shown) {
                    Write-Host -NoNewline ("`r      {0,3}%  {1:N1} of {2:N1} MB" -f $pct, ($got / 1MB), ($total / 1MB))
                    $shown = $pct
                }
            }
        }
        Write-Host ""
    }
    finally {
        $fs.Close(); $in.Close(); $resp.Close()
    }
}

# SHA256SUMS lines are "<hash>  <name>" (or "*<name>", binary mode)
function Get-ExpectedHash($sums, $name) {
    Get-Content $sums | ForEach-Object {
        $hash, $n = $_ -split '\s+\*?', 2
        if ($n -eq $name) { $hash }
    } | Select-Object -First 1
}

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ostraka-" + [Guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
# -Clean runs the uninstaller beside this script (the repository's desktop\, or desktop\ in an unpacked
# %USERPROFILE%\Ostraka). Copied out first: it may delete the tree this script is running from
if ($Clean) {
    $ostrakaHome = if ($env:OSTRAKA_HOME) { $env:OSTRAKA_HOME } else { Join-Path $env:USERPROFILE "Ostraka" }
    $uninstaller = @((Join-Path $PSScriptRoot "uninstall.ps1"), (Join-Path $ostrakaHome "desktop\uninstall.ps1")) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $uninstaller) {
        Remove-Item -Recurse -Force $tmp
        throw "-Clean needs uninstall.ps1 beside install.ps1"
    }
    Copy-Item $uninstaller (Join-Path $tmp "uninstall.ps1")
}

Write-Host ""
Write-Host "  Ostraka: installer" -ForegroundColor White
Write-Host "  ostraka: pieces of broken pottery, written on for centuries" -ForegroundColor DarkGray
$script:steps = $(if ($File) { 0 } elseif ($FromDir) { 2 } else { 3 }) +
    $(if ($DownloadOnly) { 0 } else { 2 }) + $(if ($Clean -and -not $DownloadOnly) { 1 } else { 0 })

try {
    if (-not $File) {
        if ($FromDir) {
            Step "Finding the package in $FromDir"
            if (-not (Test-Path $FromDir -PathType Container)) { throw "No such folder: $FromDir" }
            $pkg = Get-ChildItem $FromDir -Filter $pattern | Select-Object -First 1
            if (-not $pkg) { throw "$FromDir has no $pattern" }
            if (-not (Test-Path (Join-Path $FromDir "SHA256SUMS"))) { throw "$FromDir has no SHA256SUMS" }
            $name = $pkg.Name
            Copy-Item $pkg.FullName (Join-Path $tmp $name)
            Copy-Item (Join-Path $FromDir "SHA256SUMS") (Join-Path $tmp "SHA256SUMS")
            Done $name
        }
        else {
            Step "Finding the $Version release of $repo"
            $token = if ($env:GH_TOKEN) { $env:GH_TOKEN } elseif ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { $null }
            if (-not $token -and (Get-Command gh -ErrorAction SilentlyContinue)) {
                # A gh that isn't signed in writes to stderr, which Windows PowerShell 5.1 turns into a
                # terminating error under ErrorActionPreference Stop: ask it with Continue
                $token = & { $ErrorActionPreference = "Continue"; try { gh auth token 2>$null } catch { $null } }
                if ($LASTEXITCODE -ne 0) { $token = $null }
            }
            if (-not $token) { throw "Set GH_TOKEN to a token that can read $repo, or log in with gh auth login" }
            $auth = @{ Authorization = "Bearer $token" }
            $path = if ($Version -eq "latest") { "releases/latest" } else { "releases/tags/$Version" }
            $release = Invoke-RestMethod -Headers ($auth + @{ Accept = "application/vnd.github+json" }) "https://api.github.com/repos/$repo/$path"
            $asset = $release.assets | Where-Object { $_.name -like $pattern } | Select-Object -First 1
            $sums = $release.assets | Where-Object { $_.name -eq "SHA256SUMS" } | Select-Object -First 1
            if (-not $asset) { throw "The $Version release has no $pattern" }
            if (-not $sums) { throw "The $Version release has no SHA256SUMS" }
            $name = $asset.name
            Done "$($release.tag_name): $name"

            Step "Downloading $name"
            $raw = $auth + @{ Accept = "application/octet-stream" }
            Get-Download $asset.url $raw (Join-Path $tmp $name)
            Invoke-WebRequest -UseBasicParsing -Headers $raw -OutFile (Join-Path $tmp "SHA256SUMS") $sums.url
            Done "Downloaded"
        }

        # Before anything else touches it: a package that doesn't match is never opened or installed
        Step "Checking it against the release's SHA256SUMS"
        $File = Join-Path $tmp $name
        $expected = Get-ExpectedHash (Join-Path $tmp "SHA256SUMS") $name
        if (-not $expected) { throw "SHA256SUMS doesn't list $($name): not installing it" }
        $actual = (Get-FileHash -Algorithm SHA256 $File).Hash
        if ($actual -ne $expected) { throw "$name doesn't match SHA256SUMS: not installing it" }
        Done "SHA-256 matches"
    }
    if (-not (Test-Path $File -PathType Leaf)) { throw "No such file: $File" }
    $File = (Resolve-Path $File).Path

    if ($DownloadOnly) {
        New-Item -ItemType Directory -Force $DownloadOnly | Out-Null
        Copy-Item $File $DownloadOnly
        $sumsFile = Join-Path $tmp "SHA256SUMS"
        if (Test-Path $sumsFile) { Copy-Item $sumsFile $DownloadOnly }
        Write-Host ""
        Write-Host "  Saved $(Split-Path $File -Leaf) in $DownloadOnly; nothing installed." -ForegroundColor Green
        return
    }

    if ($Clean) {
        Step "Removing the current install and its data (uninstall.ps1 -Data)"
        $uargs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $tmp "uninstall.ps1"), "-Data")
        if ($Yes) { $uargs += "-Yes" }
        & powershell @uargs
        if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 failed ($LASTEXITCODE): not installing over it" }
        Done "Uninstalled"
    }

    Step "Installing $(Split-Path $File -Leaf)"
    Note "Windows shows its own progress window while it installs"
    $log = Join-Path $tmp "install.log"
    # /passive: Windows' progress window, no questions. The MSI is per-user (no UAC prompt).
    # SERVERURL writes the server policy (desktop/README.md), which locks it and skips the first-run screen
    $margs = @("/i", "`"$File`"", "/passive", "/l*v", "`"$log`"")
    if ($ServerUrl) { $margs += "SERVERURL=`"$ServerUrl`"" }
    $p = Start-Process msiexec.exe -ArgumentList $margs -Wait -PassThru
    # 3010: installed, a restart is pending for something else
    if ($p.ExitCode -notin 0, 3010) {
        Get-Content $log -Tail 40
        throw "msiexec failed with $($p.ExitCode)"
    }
    Done "Installed$(if ($p.ExitCode -eq 3010) { ' (Windows wants a restart for something else)' })"

    Step "Finding the Start menu shortcut"
    $menus = @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("CommonPrograms"))
    $lnk = $menus | ForEach-Object { Get-ChildItem $_ -Recurse -Depth 2 -Filter "Ostraka.lnk" -ErrorAction SilentlyContinue } |
        Select-Object -First 1
    if ($lnk) { Done $lnk.FullName } else { Done "No Ostraka.lnk in the Start menu: open Ostraka from Settings > Apps" }

    $took = (Get-Date) - $started
    Write-Host ""
    Write-Host ("  Ostraka is installed ({0}m {1}s). Open it from the Start menu." -f [math]::Floor($took.TotalMinutes), $took.Seconds) -ForegroundColor Green
    if (-not $ServerUrl) {
        Write-Host "  On first launch it asks for your team's server, or runs Ostraka on this computer (that needs Docker Desktop)." -ForegroundColor DarkGray
    }
    if ($Launch -and $lnk) { Start-Process $lnk.FullName }
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
