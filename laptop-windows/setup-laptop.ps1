# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - Alienware laptop setup  (needs admin)
#
# Run with: setup-laptop.cmd          (set up)
#           undo-laptop.cmd           (put the Ethernet port back to normal DHCP)
#
#   * Ethernet port -> static 10.48.0.20/24, no gateway (the cable to the
#     case side panel). Internet stays on Wi-Fi.
#   * Firewall: allow traffic from the case network (PC + Pi)
#   * Installs PowerToys for Mouse Without Borders
#
# If you ever plug this Ethernet port into a router instead, run undo-laptop.cmd.
# ---------------------------------------------------------------------------
param(
    [string]$CaseIP = '10.48.0.20',
    [int]$PrefixLength = 24,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $extra = if ($Undo) { ' -Undo' } else { '' }
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"$extra"
    exit
}
function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

function Select-CaseAdapter([string]$WantIP) {
    $eth = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 14 } | Sort-Object Name)
    if ($eth.Count -eq 0) { throw 'No wired Ethernet port found. Use a USB-C Ethernet adapter and run this again.' }
    $guess = $null
    $rows = for ($i = 0; $i -lt $eth.Count; $i++) {
        $a = $eth[$i]
        $ip = (Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
        $gw = (Get-NetIPConfiguration -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue).IPv4DefaultGateway
        if (-not $guess -and $ip -eq $WantIP) { $guess = $i }
        [pscustomobject]@{ '#' = $i + 1; Name = $a.Name; Status = $a.Status; MAC = $a.MacAddress
                           IPv4 = $ip; Gateway = if ($gw) { $gw.NextHop } else { '' }; Idx = $i
                           NoDhcp = ($a.Status -eq 'Up' -and (-not $gw) -and ((-not $ip) -or $ip -like '169.254.*')) }
    }
    if ($null -eq $guess) { $g = $rows | Where-Object NoDhcp | Select-Object -First 1; if ($g) { $guess = $g.Idx } }
    if ($null -eq $guess -and $eth.Count -eq 1) { $guess = 0 }
    $rows | Format-Table '#', Name, Status, MAC, IPv4, Gateway -AutoSize | Out-Host
    $def = if ($null -ne $guess) { $guess + 1 } else { $null }
    while ($true) {
        $prompt = if ($def) { "Which port is cabled to the deck's side panel? [$def]" } else { "Which port is cabled to the deck's side panel? (number)" }
        $ans = Read-Host $prompt
        if (-not $ans -and $def) { $ans = $def }
        $n = 0
        if ([int]::TryParse("$ans", [ref]$n) -and $n -ge 1 -and $n -le $eth.Count) { return $eth[$n - 1] }
        Warn 'Type one of the numbers in the # column.'
    }
}

$nic = Select-CaseAdapter $CaseIP

if ($Undo) {
    Say "Putting $($nic.Name) back to automatic (DHCP)"
    Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -Dhcp Enabled -AutomaticMetric Enabled
    Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ResetServerAddresses
    Set-DnsClient -InterfaceIndex $nic.ifIndex -RegisterThisConnectionsAddress $true
    Write-Host 'Done. (The firewall rule for 10.48.0.0/24 is harmless and was left in place.)'
    Read-Host 'Press Enter to close'; exit
}

Say "Setting $($nic.Name) to $CaseIP/$PrefixLength (no gateway)"
Set-NetIPInterface -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -Dhcp Disabled -InterfaceMetric 500
Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Get-NetRoute -InterfaceIndex $nic.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $CaseIP -PrefixLength $PrefixLength | Out-Null
Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ResetServerAddresses
Set-DnsClient -InterfaceIndex $nic.ifIndex -RegisterThisConnectionsAddress $false
try { Set-NetConnectionProfile -InterfaceIndex $nic.ifIndex -NetworkCategory Private -ErrorAction Stop } catch { }

Say 'Firewall rule for the case network'
Get-NetFirewallRule -DisplayName 'Cyberdeck - case network' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName 'Cyberdeck - case network' -Direction Inbound -Action Allow `
    -RemoteAddress 10.48.0.0/24 -Profile Any | Out-Null

Say 'PowerToys'
if (Get-Command winget -ErrorAction SilentlyContinue) {
    $listed = winget list --id Microsoft.PowerToys -e --accept-source-agreements 2>$null | Select-String -SimpleMatch 'Microsoft.PowerToys'
    if ($listed) { Write-Host 'Already installed.' }
    else { winget install --id Microsoft.PowerToys -e --accept-source-agreements --accept-package-agreements }
} else { Warn 'winget missing - install PowerToys from the Microsoft Store.' }

Say 'Testing the link to the PC and the Pi'
foreach ($h in @{ 'PC' = '10.48.0.10'; 'Pi' = '10.48.0.2' }.GetEnumerator()) {
    $ok = Test-Connection -ComputerName $h.Value -Count 1 -Quiet -ErrorAction SilentlyContinue
    Write-Host ("{0,-4} {1,-12} {2}" -f $h.Key, $h.Value, $(if ($ok) { 'reachable' } else { 'no answer yet (is it on and set up?)' }))
}

Write-Host @"

============================================================
 Laptop = $CaseIP on "$($nic.Name)".   This PC's name: $env:COMPUTERNAME

 Mouse Without Borders (PowerToys > Mouse Without Borders), on BOTH PCs:
   * Use the same security key on both (generate on one, type into the other)
   * "IP address mapping" box - paste these two lines (use the real names):
        $env:COMPUTERNAME 10.48.0.20
        <PC computer name> 10.48.0.10
     The PC's name is in k12-info.txt on this USB drive.
   * Untick "Use Service" unless you need it on the login screen.
============================================================
"@
Read-Host 'Press Enter to close'
