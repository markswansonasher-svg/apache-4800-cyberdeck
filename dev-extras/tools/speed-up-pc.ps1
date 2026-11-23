# ---------------------------------------------------------------------------
# Speed-ups for game dev on this PC. Needs admin (it asks).
#
#   speed-up-pc.ps1                    Defender skips dev build folders (safe to run again;
#                                      run it after making new Unity/Godot projects)
#   speed-up-pc.ps1 -MemoryIntegrity off|on    also turn Memory integrity off / back on (restart after)
#
# Skipped folders are only ones tools regenerate themselves: Unity's Library and Temp,
# Godot's .godot cache, the Minecraft server's data, and Docker's disk image.
# Your own scripts, scenes and assets are still scanned.
# A log of each run is kept in speed-up-pc.log next to this script.
# ---------------------------------------------------------------------------
param([string]$MemoryIntegrity = '')

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    if ($env:SPEEDUP_RELAUNCHED) { Write-Host 'Could not get admin rights.'; Read-Host 'Press Enter to close'; return }
    $env:SPEEDUP_RELAUNCHED = '1'
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($MemoryIntegrity) { $a += " -MemoryIntegrity $MemoryIntegrity" }
    Start-Process powershell -Verb RunAs -ArgumentList $a
    return
}

$log = Join-Path $PSScriptRoot 'speed-up-pc.log'
try { Start-Transcript $log -Force | Out-Null } catch { }
try {
    # --- find projects: Unity Hub's list, plus a quick look in the usual places (not AppData/OneDrive internals)
    Write-Host 'Looking for Unity and Godot projects...'
    $unity = @(); $godot = @()
    foreach ($f in "$env:APPDATA\UnityHub\projects-v1.json", "$env:APPDATA\UnityHub\projects.json") {
        try { $j = Get-Content $f -Raw -ErrorAction Stop | ConvertFrom-Json; $unity += @($j.data.PSObject.Properties.Value | ForEach-Object { $_.path }) } catch { }
    }
    $roots = @($env:USERPROFILE, "$env:USERPROFILE\Desktop", "$env:USERPROFILE\Documents", "$env:USERPROFILE\OneDrive\Desktop",
               "$env:USERPROFILE\OneDrive\Documents", "$env:USERPROFILE\Projects", 'C:\Projects', 'C:\deck') | Where-Object { Test-Path $_ }
    foreach ($r in $roots) {
        # two levels down is where projects live (Desktop\MyGame, Documents\Unity\MyGame)
        foreach ($d in @(Get-Item $r) + @(Get-ChildItem $r -Directory -ErrorAction SilentlyContinue) +
                       @(Get-ChildItem $r -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(AppData|OneDrive.*|\..*)$' } |
                         ForEach-Object { Get-ChildItem $_.FullName -Directory -ErrorAction SilentlyContinue })) {
            if (Test-Path (Join-Path $d.FullName 'ProjectSettings\ProjectVersion.txt')) { $unity += $d.FullName }
            if (Test-Path (Join-Path $d.FullName 'project.godot')) { $godot += $d.FullName }
        }
    }
    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($p in ($unity | Where-Object { $_ } | Sort-Object -Unique)) { Write-Host "  Unity: $p"; foreach ($s in 'Library', 'Temp', 'Logs', 'obj') { $paths.Add((Join-Path $p $s)) } }
    foreach ($p in ($godot | Sort-Object -Unique)) { Write-Host "  Godot: $p"; $paths.Add((Join-Path $p '.godot')) }
    $paths.Add('C:\deck\game-servers\data')            # Minecraft server world/mods
    $paths.Add("$env:LOCALAPPDATA\Docker\wsl")         # Docker's disk image
    $paths.Add("$env:LOCALAPPDATA\Unity\cache")        # Unity package cache
    $paths.Add("$env:APPDATA\UnityHub\cache")

    $have = @((Get-MpPreference).ExclusionPath)
    $new = @($paths | Sort-Object -Unique | Where-Object { $have -notcontains $_ })
    if ($new) { Add-MpPreference -ExclusionPath $new }
    Write-Host "`nDefender now skips:" -ForegroundColor Cyan
    (Get-MpPreference).ExclusionPath | Sort-Object | ForEach-Object { Write-Host "  $_" }

    if ($MemoryIntegrity -in 'off', 'on') {
        $k = 'HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity'
        if (-not (Test-Path $k)) { New-Item $k -Force | Out-Null }
        Set-ItemProperty $k -Name Enabled -Value ([int]($MemoryIntegrity -eq 'on')) -Type DWord
        Write-Host "`nMemory integrity set to $MemoryIntegrity. Restart the PC for it to apply." -ForegroundColor Yellow
        Write-Host "(Windows Security may show a notice about it - that's expected.)"
    }
    Write-Host "`nDone." -ForegroundColor Green
} catch {
    Write-Host "`nSomething went wrong: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log: $log"
}
try { Stop-Transcript | Out-Null } catch { }
Read-Host "`nPress Enter to close"
