@echo off
rem Double-click me. Saves your CurseForge API key on this PC (NOT on the USB drive),
rem so the Minecraft server can install modpacks. Safe to run again to change the key.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-curseforge.ps1"
