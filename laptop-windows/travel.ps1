# ---------------------------------------------------------------------------
# Travel mode on the laptop: get internet through the deck's Pi.  (asks for admin)
#
#   travel-on.cmd   route internet + DNS through the Pi (10.48.0.2) and turn
#                   the laptop's Wi-Fi off
#   travel-off.cmd  undo: back to the laptop's own Wi-Fi
#
# Turn travel mode on in the Deck Panel first (Wi-Fi & travel card).
# ---------------------------------------------------------------------------
param([ValidateSet('on', 'off')][string]$Mode = 'on')
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Mode $Mode"
    exit
}
$PiIP = '10.48.0.2'
$state = Join-Path $env:ProgramData 'cyberdeck-travel.json'

$a = Get-NetIPAddress -IPAddress '10.48.0.20' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $a) {
    Write-Host 'The laptop has no 10.48.0.20 address - run setup-laptop.cmd first and plug in the deck cable.' -ForegroundColor Yellow
    Read-Host 'Press Enter to close'; exit 1
}
$idx = $a.InterfaceIndex
Get-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false

if ($Mode -eq 'on') {
    New-NetRoute -InterfaceIndex $idx -DestinationPrefix '0.0.0.0/0' -NextHop $PiIP -RouteMetric 0 | Out-Null
    Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses $PiIP
    $wifi = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 -and $_.Status -ne 'Disabled' })
    foreach ($w in $wifi) { Disable-NetAdapter -Name $w.Name -Confirm:$false }
    @{ wifi = @($wifi | ForEach-Object { $_.Name }) } | ConvertTo-Json | Set-Content $state
    Clear-DnsClientCache
    Write-Host "`nTravel mode ON: internet goes through the deck. Wi-Fi turned off: $(($wifi | ForEach-Object Name) -join ', ')" -ForegroundColor Green
    Write-Host 'If a hotel/school login page is needed, open the link on the Deck Panel (http://10.48.0.2:8080).'
} else {
    Set-DnsClientServerAddress -InterfaceIndex $idx -ResetServerAddresses
    $names = @()
    if (Test-Path $state) { try { $names = @((Get-Content $state -Raw | ConvertFrom-Json).wifi) } catch { }; Remove-Item $state -Force }
    if (-not $names) { $names = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 } | ForEach-Object Name) }
    foreach ($n in $names) { if ($n) { Enable-NetAdapter -Name $n -Confirm:$false -ErrorAction SilentlyContinue } }
    Clear-DnsClientCache
    Write-Host "`nTravel mode OFF: Wi-Fi back on, the deck cable is local-only again." -ForegroundColor Green
}
Read-Host 'Press Enter to close'
