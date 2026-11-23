# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - GitHub Actions self-hosted runner on the PC (admin)
#
# Run with: install-runner.cmd
#
# Before you run it: on GitHub open your repo > Settings > Actions > Runners >
# "New self-hosted runner" > Windows. Copy the repo URL and the token shown in
# the "Configure" box (it expires after about an hour). The token is typed in
# here and never saved to this USB drive.
#
# SAFETY: only use a self-hosted runner with PRIVATE repos. On a public repo,
# anyone who opens a pull request could run code on your PC.
#
# In a workflow, target it with:   runs-on: [self-hosted, k12]
# ---------------------------------------------------------------------------
param([string]$RunnerDir = 'C:\actions-runner')
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$url = Read-Host 'Repo URL (e.g. https://github.com/you/your-repo)'
if ($url -notmatch '^https://github\.com/[^/]+(/[^/]+)?/?$') { throw "That doesn't look like a GitHub repo or org URL: $url" }
$tokSecure = Read-Host 'Runner token (from the Configure box)' -AsSecureString
$token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokSecure))

if (-not (Test-Path "$RunnerDir\config.cmd")) {
    Write-Host 'Downloading the latest runner from GitHub...' -ForegroundColor Cyan
    $rel = Invoke-RestMethod 'https://api.github.com/repos/actions/runner/releases/latest' -Headers @{ 'User-Agent' = 'deck-setup' }
    $asset = $rel.assets | Where-Object { $_.name -like 'actions-runner-win-x64-*.zip' } | Select-Object -First 1
    if (-not $asset) { throw 'Could not find the Windows x64 runner in the latest release.' }
    New-Item -ItemType Directory -Force $RunnerDir | Out-Null
    $zip = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing

    # Check the download against the SHA-256 GitHub publishes in the release notes
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($rel.body -match "(?im)win-x64[^\r\n]*?([0-9a-f]{64})") {
        if ($Matches[1].ToLower() -ne $hash) { Remove-Item $zip; throw 'Checksum mismatch - download deleted.' }
        Write-Host 'Checksum OK.'
    } else { Write-Host '(No checksum found in the release notes; skipped check.)' -ForegroundColor Yellow }

    Expand-Archive $zip -DestinationPath $RunnerDir -Force
    Remove-Item $zip
}

Push-Location $RunnerDir
try {
    .\config.cmd --unattended --url $url --token $token --name "$env:COMPUTERNAME-deck" `
        --labels 'k12,deck' --work '_work' --runasservice --replace
    if ($LASTEXITCODE -ne 0) { throw "config.cmd failed (exit $LASTEXITCODE). Tokens expire fast - get a new one and retry." }
} finally { Pop-Location; $token = $null }

Write-Host @"

Runner installed as a Windows service and starts with the PC.
Use it in a workflow with:   runs-on: [self-hosted, k12]
Remove it later with:        cd $RunnerDir ; .\config.cmd remove --token <removal token>
"@ -ForegroundColor Green
Read-Host 'Press Enter to close'
