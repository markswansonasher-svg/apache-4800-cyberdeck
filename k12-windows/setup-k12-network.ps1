# ---------------------------------------------------------------------------
# Apache 4800 cyberdeck - PC network, wake-up and power setup  (needs admin)
#
# Run with: setup-k12-network.cmd  (double-click; it asks for admin)
# Safe to run again.
#
#   * The Ethernet port (cabled to the case switch) -> static 10.48.0.10/24,
#     no gateway, so internet stays on Wi-Fi (or the Pi in travel mode).
#     The UM870 Slim has one Ethernet port; a PC with two works too (you pick).
#   * Firewall: allow everything from the case network (Pi + laptop only),
#     and Minecraft (TCP 25565) from anywhere
#   * Wake-on-LAN settings on that adapter, Fast Startup off
#   * Power plan: never sleep on AC (24/7 server)
#   * Saves the MAC address to k12-info.txt on this USB drive for the Pi
# ---------------------------------------------------------------------------
param(
    [string]$CaseIP = '10.48.0.10',
    [int]$PrefixLength = 24
)
$ErrorActionPreference = 'Stop'

# --- self-elevate -----------------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

function Say($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "!!  $msg" -ForegroundColor Yellow }

# --- pick the case-network adapter -----------------------------------------
function Select-CaseAdapter([string]$WantIP) {
    # NdisPhysicalMedium 14 = wired Ethernet
    $eth = @(Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 14 } | Sort-Object Name)
    if ($eth.Count -eq 0) { throw 'No wired Ethernet adapters found.' }

    $guess = $null
    $rows = for ($i = 0; $i -lt $eth.Count; $i++) {
        $a = $eth[$i]
        $ip = (Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress
        $gw = (Get-NetIPConfiguration -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue).IPv4DefaultGateway
        # Already ours, or plugged in but no DHCP (169.254.x / no gateway) = case switch
        if (-not $guess -and $ip -eq $WantIP) { $guess = $i }
        [pscustomobject]@{ '#' = $i + 1; Name = $a.Name; Status = $a.Status; MAC = $a.MacAddress
                           IPv4 = $ip; Gateway = if ($gw) { $gw.NextHop } else { '' } ; Idx = $i
                           NoDhcp = ($a.Status -eq 'Up' -and (-not $gw) -and ((-not $ip) -or $ip -like '169.254.*')) }
    }
    if ($null -eq $guess) { $g = $rows | Where-Object NoDhcp | Select-Object -First 1; if ($g) { $guess = $g.Idx } }

    Write-Host ''
    $rows | Format-Table '#', Name, Status, MAC, IPv4, Gateway -AutoSize | Out-Host
    Write-Host 'The case-switch port is the one with a cable to the 5-port switch and no router (no gateway).'
    $def = if ($null -ne $guess) { $guess + 1 } else { $null }
    while ($true) {
        $prompt = if ($def) { "Which adapter is cabled to the case switch? [$def]" } else { 'Which adapter is cabled to the case switch? (number)' }
        $ans = Read-Host $prompt
        if (-not $ans -and $def) { $ans = $def }
        $n = 0
        if ([int]::TryParse("$ans", [ref]$n) -and $n -ge 1 -and $n -le $eth.Count) { return $eth[$n - 1] }
        Warn 'Type one of the numbers in the # column.'
    }
}

Say 'Choosing the case-network adapter (the PC Ethernet port cabled to the case switch)'
$nic = Select-CaseAdapter $CaseIP
Write-Host "Using: $($nic.Name)  ($($nic.InterfaceDescription))  MAC $($nic.MacAddress)"

# --- static IP, no gateway --------------------------------------------------
Say "Setting $($nic.Name) to $CaseIP/$PrefixLength (no gateway)"
Set-NetIPInterface -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -Dhcp Disabled -InterfaceMetric 500
Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Get-NetRoute -InterfaceIndex $nic.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $CaseIP -PrefixLength $PrefixLength | Out-Null
Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ResetServerAddresses
Set-DnsClient -InterfaceIndex $nic.ifIndex -RegisterThisConnectionsAddress $false
try { Set-NetConnectionProfile -InterfaceIndex $nic.ifIndex -NetworkCategory Private -ErrorAction Stop }
catch { Warn 'Could not mark the case network Private (normal for an unidentified network). The firewall rule below covers it.' }

# --- firewall ---------------------------------------------------------------
Say 'Firewall rules'
foreach ($r in 'Cyberdeck - case network', 'Cyberdeck - Minecraft') {
    Get-NetFirewallRule -DisplayName $r -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}
# Only the Pi and the laptop live on 10.48.0.0/24 - it's a cable inside the case.
New-NetFirewallRule -DisplayName 'Cyberdeck - case network' -Direction Inbound -Action Allow `
    -RemoteAddress 10.48.0.0/24 -Profile Any | Out-Null
New-NetFirewallRule -DisplayName 'Cyberdeck - Minecraft' -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 25565 -Profile Any | Out-Null
Write-Host 'Allowed: all traffic from 10.48.0.0/24, and TCP 25565 (Minecraft).'

# --- Wake-on-LAN ------------------------------------------------------------
Say 'Wake-on-LAN on the case adapter'
try {
    Set-NetAdapterPowerManagement -Name $nic.Name -WakeOnMagicPacket Enabled -ErrorAction Stop
    Write-Host 'Wake on Magic Packet: Enabled'
} catch { Warn "Set-NetAdapterPowerManagement failed: $($_.Exception.Message)" }

# Driver "Advanced" settings differ between Intel and Realtek; set whatever exists.
$wanted = @(
    @{ Match = '*Wake on Magic Packet*';  Value = 'Enabled' },
    @{ Match = '*Shutdown Wake*';         Value = 'Enabled' },   # Realtek: Shutdown Wake-On-Lan
    @{ Match = '*Wake from power off*';   Value = 'Enabled' },
    @{ Match = '*WOL & Shutdown Link*';   Value = '10 Mbps First' },
    @{ Match = '*Energy Efficient Ethernet*'; Value = 'Disabled' },  # EEE can drop WoL links
    @{ Match = '*Green Ethernet*';        Value = 'Disabled' }
)
$props = Get-NetAdapterAdvancedProperty -Name $nic.Name -ErrorAction SilentlyContinue
foreach ($w in $wanted) {
    foreach ($p in ($props | Where-Object { $_.DisplayName -like $w.Match })) {
        if ($p.ValidDisplayValues -contains $w.Value) {
            try { Set-NetAdapterAdvancedProperty -Name $nic.Name -DisplayName $p.DisplayName -DisplayValue $w.Value -ErrorAction Stop
                  Write-Host "$($p.DisplayName): $($w.Value)" }
            catch { Warn "Couldn't set $($p.DisplayName)" }
        }
    }
}
# Let the NIC wake the PC (Device Manager > Power Management checkbox)
try {
    $inst = (Get-PnpDevice | Where-Object { $_.FriendlyName -eq $nic.InterfaceDescription } | Select-Object -First 1).InstanceId
    if ($inst) {
        $wake = Get-CimInstance -Namespace root\wmi -ClassName MSPower_DeviceWakeEnable -ErrorAction Stop |
                Where-Object { $_.InstanceName -like "$inst*" }
        if ($wake) { $wake | Set-CimInstance -Property @{ Enable = $true } ; Write-Host 'Allow this device to wake the computer: On' }
    }
} catch { Warn 'Check by hand: Device Manager > adapter > Power Management > "Allow this device to wake the computer".' }

# Fast Startup makes Windows "hibernate" on shutdown, which breaks wake from off on many NICs.
Say 'Turning off Fast Startup'
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -Value 0 -Type DWord

# --- power plan -------------------------------------------------------------
Say 'Power plan: never sleep on AC (24/7 servers)'
powercfg /change standby-timeout-ac 0   | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
powercfg /change monitor-timeout-ac 15  | Out-Null
Write-Host 'Sleep: never. Screen off after 15 min.'

# --- save info for the Pi ---------------------------------------------------
$mac = $nic.MacAddress -replace '-', ':'
$infoPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'k12-info.txt'
@"
PC computer name  : $env:COMPUTERNAME
Case adapter      : $($nic.Name) ($($nic.InterfaceDescription))
Case IP           : $CaseIP
MAC (for the Pi)  : $mac
Written           : $(Get-Date -Format 'yyyy-MM-dd HH:mm')

On the Pi, if auto-detect didn't work:  sudo deck-set-mac $mac
"@ | Set-Content -Path $infoPath -Encoding ASCII
Write-Host "`nSaved to $infoPath" -ForegroundColor Green

# --- manual checklist -------------------------------------------------------
Write-Host @"

============================================================
 Network part done.  PC = $CaseIP   MAC = $mac

 Things a script can't do - please do these by hand:

 1. BIOS (tap DEL at power-on):
      * Wake on LAN / "Power On By PCI-E" / "Wake by LAN" = Enabled
      * "Restore on AC Power Loss" / "AC Back" = Power On
      * ErP / Deep Sleep = Disabled (it cuts NIC power when off)
      * Power mode / TDP = a lower setting if the BIOS has one, not the
        highest (less heat inside the closed case)
 2. Shure MOTIV Mix app: set the MV7+ USB output with noise reduction +
    auto level on (that's the feed Discord on the PC uses).
 3. Mouse Without Borders: see README (IP address mapping).
 4. Next: run install-apps.cmd (NOT as admin).
 5. Restart the PC once so Fast Startup = off takes effect.
============================================================
"@
Read-Host 'Press Enter to close'
