# ---------------------------------------------------------------------------
# Builds Deck.exe (the Deck app) from DeckApp.cs with the C# compiler that comes
# with Windows (.NET Framework 4) - nothing to install.
#
#   powershell -File build-app.ps1 [-Out C:\deck\app\Deck.exe]
#
# setup-k12-agent.cmd runs this. Exit code 0 = built.
# ---------------------------------------------------------------------------
param([string]$Out = (Join-Path $PSScriptRoot 'Deck.exe'))
$ErrorActionPreference = 'Stop'
$csc = @("$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
         "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) { Write-Host '.NET Framework 4 C# compiler not found (it comes with Windows 10/11).'; exit 1 }
$src = @(Get-ChildItem $PSScriptRoot -Filter *.cs | ForEach-Object FullName)   # every .cs here: the app, standalone mode (DeckLocal.cs), Dev tools (DevTools.cs)
$ico = Join-Path $PSScriptRoot 'deck.ico'
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$a = @('/nologo', '/target:winexe', '/optimize+', '/codepage:65001', '/platform:anycpu',
       "/out:$Out", '/r:System.Web.Extensions.dll', '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll', '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll')
if (Test-Path $ico) { $a += "/win32icon:$ico" }
& $csc @a @src
if ($LASTEXITCODE -ne 0) { Write-Host "Building the Deck app failed (see above)."; exit 1 }
Write-Host "Built $Out"
exit 0
